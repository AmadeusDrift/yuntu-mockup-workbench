"""Compile supported PSDs into reusable raster/geometry templates and render batches."""
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
import numpy as np
from PIL import Image, ImageChops, ImageCms, ImageFilter, ImageOps

VERSION = 4
BATCH_WORKERS = 2
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.bmp'}
MODES = {'auto', 'contain', 'cover', 'cover_bleed', 'stretch'}
ORIENTATIONS = {'auto', 'original', 'cw90', 'ccw90', 'rotate180'}


def safe_name(value):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', value).strip().rstrip('.')[:100]
    if not value or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        value = '_' + (value or 'image')
    return value


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.part')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def unique_dir(parent, prefix):
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        path = parent / (safe_name(prefix) + '_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:5])
        try:
            path.mkdir()
            return path
        except FileExistsError:
            pass
    raise ValueError('无法建立输出文件夹')


def coefficients(quad, size):
    w, h = size
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    a, b = [], []
    for (x, y), (u, v) in zip(quad, src):
        a += [[x,y,1,0,0,0,-u*x,-u*y], [0,0,0,x,y,1,-v*x,-v*y]]
        b += [u,v]
    try:
        result = np.linalg.solve(np.array(a, dtype=float), np.array(b, dtype=float))
    except np.linalg.LinAlgError as exc:
        raise ValueError('产品层四角位置无效') from exc
    if not np.all(np.isfinite(result)):
        raise ValueError('产品层变换包含无效数值')
    return result.tolist()


def apply_opacity(image, opacity):
    if opacity != 255:
        image = image.copy()
        image.putalpha(image.getchannel('A').point(lambda n: round(n * opacity / 255)))
    return image


def shadow_image(layer, shadow):
    if not shadow:
        return None
    alpha = layer.getchannel('A')
    spread = max(0, shadow.get('spread', 0))
    if spread:
        radius = min(128, math.ceil(spread))
        alpha = alpha.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    blur = max(0, shadow['blur'] - spread)
    if blur:
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur / 2))
    alpha = alpha.transform(alpha.size, Image.Transform.AFFINE,
                            (1,0,-shadow['dx'],0,1,-shadow['dy']), Image.Resampling.BICUBIC)
    alpha = alpha.point(lambda n: round(n * shadow['opacity'] / 100))
    ink = Image.new('RGBA', layer.size, tuple(shadow['color']) + (0,))
    ink.putalpha(alpha)
    return ink


def add_shadow(canvas, layer, shadow):
    ink = shadow_image(layer, shadow)
    return Image.alpha_composite(canvas, ink) if ink is not None else canvas


class Compiler:
    def __init__(self, folder, product_name):
        self.folder = Path(folder)
        self.product_name = product_name
        self.index = 0
        self.warnings = []
        self.progress = lambda text:None

    def asset(self, image):
        self.index += 1
        name = f'asset_{self.index:04d}.png'
        image.save(self.folder / name)
        return name

    def check_layer(self, layer, label):
        from psd_tools.constants import BlendMode, Tag
        if layer.kind=='artboard':
            raise ValueError(label+'：暂不支持画板文档，请在副本中整理为普通场景组')
        if layer.blend_mode not in (BlendMode.NORMAL, BlendMode.PASS_THROUGH, BlendMode.MULTIPLY,
                                    BlendMode.SCREEN, BlendMode.SOFT_LIGHT, BlendMode.DARKEN):
            raise ValueError(label + '：暂不支持混合模式 ' + str(layer.blend_mode))
        if layer.fill_opacity != 255:
            raise ValueError(label + '：暂不支持非 100% 的填充不透明度')
        from blend_if import parse_blend_if
        try:blend_if=parse_blend_if(layer)
        except ValueError as exc:raise ValueError(label+'：'+str(exc)) from exc
        if blend_if and (layer.has_effects() or layer.kind=='brightnesscontrast' or
                         (layer.is_group() and layer.blend_mode==BlendMode.PASS_THROUGH)):
            raise ValueError(label+'：混合颜色带暂不能与图层样式、调整层或穿透组组合使用')
        if Tag.KNOCKOUT_SETTING in layer.tagged_blocks:
            knockout = layer.tagged_blocks.get_data(Tag.KNOCKOUT_SETTING)
            if int(getattr(knockout, 'value', knockout)):
                raise ValueError(label + '：暂不支持挖空效果')
        if blend_if:self.warnings.append('下层灰度混合颜色带采用渐变透明度近似，请先核对高光和暗部。')
        return blend_if

    @staticmethod
    def blend_name(layer):
        from psd_tools.constants import BlendMode
        return {BlendMode.MULTIPLY:'multiply', BlendMode.SCREEN:'screen',
                BlendMode.SOFT_LIGHT:'soft_light', BlendMode.DARKEN:'darken'}.get(layer.blend_mode, 'normal')

    @staticmethod
    def check_clip_base(layer, label, blend_if):
        from psd_tools.constants import BlendMode, Tag
        if layer.opacity!=255:
            raise ValueError(label+f'：剪贴基层不透明度为 {layer.opacity}/255，目前需为 100%')
        if blend_if:
            raise ValueError(label+'：剪贴基层使用了混合颜色带，目前暂不支持此组合')
        if not layer.tagged_blocks.get_data(Tag.BLEND_CLIPPING_ELEMENTS,1):
            raise ValueError(label+'：暂不支持关闭“将剪贴图层混合成组”的剪贴基层')

    def mask(self, layer, size, label, include_vector=True):
        result = None
        if layer.has_mask() and not layer.mask.disabled:
            m = layer.mask
            if m.flags.invert_mask or m.flags.pos_relative_to_layer:
                raise ValueError(label+'：暂不支持旧式反相或相对坐标蒙版，请在副本中重新建立像素蒙版')
            parameters = m.parameters
            if parameters and (parameters.user_mask_density not in (None,255) or parameters.user_mask_feather not in (None,0)):
                raise ValueError(label + '：暂不支持额外的蒙版浓度/羽化参数')
            # Dynamic vector masks are drawn separately. Use the pixel mask rather
            # than Photoshop's optional combined pixel+vector "real" cache.
            raw = m.topil(real=not include_vector)
            if raw is None:
                # A vector-only mask may have mask parameters but no pixel channel.
                expected_id=-2 if include_vector or not m.has_real() else -3
                channels=[info for info in layer._record.channel_info if int(info.id)==expected_id]
                if channels:
                    rect=(m.data.left,m.data.top,m.data.right,m.data.bottom) if include_vector else (m.left,m.top,m.right,m.bottom)
                    background=m.data.background_color if include_vector else m.background_color
                    # Photoshop can save an all-white adjustment mask as an
                    # empty rectangle plus the two-byte compression header.
                    if rect[0]<=rect[2] and rect[1]<=rect[3] and (rect[0]==rect[2] or rect[1]==rect[3]) and all(info.length==2 for info in channels):
                        result=Image.new('L',size,background)
                    else:raise ValueError(label + '：蒙版像素不可读取')
            else:
                if include_vector:
                    left,top,background=m.data.left,m.data.top,m.data.background_color
                else:
                    left,top,background=m.left,m.top,m.background_color
                result=Image.new('L',size,background)
                result.paste(raw.convert('L'),(left,top))
        if include_vector and layer.has_vector_mask() and not layer.vector_mask.disabled:
            vector=layer.vector_mask
            if any(path.operation not in (-1,0,1,2,3) for path in vector.paths) or (vector.paths and vector.paths[0].operation==-1):
                raise ValueError(label+'：矢量蒙版路径运算无法识别，请转为像素蒙版')
            if any(not path.is_closed() for path in vector.paths):
                raise ValueError(label+'：产品或组的矢量蒙版包含开放路径，请先在副本中整理为闭合路径')
            if vector.initial_fill_rule and vector.paths:
                raise ValueError(label+'：暂不支持此种矢量蒙版初始填充规则，请转为像素蒙版')
            params=getattr(layer._record.mask_data,'parameters',None)
            if params and (params.vector_mask_density not in (None,255) or params.vector_mask_feather not in (None,0)):
                raise ValueError(label+'：暂不支持额外的矢量蒙版浓度/羽化参数')
            from psd_tools.composite.vector import draw_vector_mask
            coverage=draw_vector_mask(layer,viewport=(0,0,*size))[:,:,0]
            if vector.inverted:coverage=1-coverage
            raster=Image.fromarray(np.rint(np.clip(coverage,0,1)*255).astype('uint8'))
            result=ImageChops.multiply(result,raster) if result is not None else raster
        return result

    def color_overlays(self, image, layer, label):
        from psd_tools.constants import BlendMode, Resource
        if not layer.has_effects():return image
        for effect in layer.effects:
            if not effect.enabled or effect.name!='ColorOverlay':continue
            if effect.blend_mode!=BlendMode.NORMAL:
                raise ValueError(label+'：当前颜色叠加仅支持普通混合模式')
            color=effect.color
            if color and all(k in color for k in (b'Rd  ',b'Grn ',b'Bl  ')):
                rgb=[min(255,max(0,round(float(color[k])))) for k in (b'Rd  ',b'Grn ',b'Bl  ')]
            elif color and all(k in color for k in (b'redFloat',b'greenFloat',b'blueFloat')):
                rgb=[min(255,max(0,round(float(color[k])*255))) for k in (b'redFloat',b'greenFloat',b'blueFloat')]
            else:raise ValueError(label+'：颜色叠加颜色格式无法识别')
            profile=layer._psd.image_resources.get_data(Resource.ICC_PROFILE)
            if profile:
                try:
                    pixel=ImageCms.profileToProfile(Image.new('RGB',(1,1),tuple(rgb)),
                        ImageCms.ImageCmsProfile(io.BytesIO(profile)),ImageCms.createProfile('sRGB'),outputMode='RGB')
                    rgb=pixel.getpixel((0,0))
                except Exception as exc:raise ValueError(label+'：无法转换颜色叠加的颜色配置文件') from exc
            strength=float(effect.opacity)/100
            if not 0<=strength<=1:raise ValueError(label+'：颜色叠加不透明度无效')
            alpha=image.getchannel('A')
            paint=Image.new('RGB',image.size,tuple(rgb))
            image=Image.blend(image.convert('RGB'),paint,strength).convert('RGBA')
            image.putalpha(alpha)
            self.warnings.append('固定层普通颜色叠加已在导入时合成；请先预览核对效果。')
        return image

    def shadow(self, layer, label, ignore_color_overlay=False):
        from psd_tools.constants import BlendMode
        if not layer.has_effects():
            return None
        enabled = [e for e in layer.effects if e.enabled and not (ignore_color_overlay and e.name=='ColorOverlay')]
        if not enabled:return None
        if len(enabled) != 1 or enabled[0].name != 'DropShadow':
            raise ValueError(label + '：只支持简单投影，其他图层样式请在 PSD 副本中预先处理')
        e = enabled[0]
        d = e.descriptor
        color = e.color
        if color and all(k in color for k in (b'Rd  ',b'Grn ',b'Bl  ')):
            rgb = [min(255,max(0,round(float(color[k])))) for k in (b'Rd  ',b'Grn ',b'Bl  ')]
        elif color and all(k in color for k in (b'redFloat',b'greenFloat',b'blueFloat')):
            rgb = [min(255,max(0,round(float(color[k])*255))) for k in (b'redFloat',b'greenFloat',b'blueFloat')]
        else:
            raise ValueError(label + '：投影颜色格式暂不支持')
        if e.blend_mode != BlendMode.NORMAL and not (e.blend_mode == BlendMode.MULTIPLY and rgb == [0,0,0]):
            raise ValueError(label + '：只支持普通投影，或黑色的正片叠底投影')
        contour = d.get(b'TrnS')
        if contour:
            curve = contour.get(b'Crv ')
            coords = [(float(p[b'Hrzn']),float(p[b'Vrtc'])) for p in curve] if curve else []
            if coords != [(0,0),(255,255)]:
                raise ValueError(label + '：暂不支持非线性投影等高线')
        if float(e.noise):
            raise ValueError(label + '：暂不支持带杂色的投影')
        spread=float(e.size)*float(e.choke)/100
        if not 0<=float(e.choke)<=100 or spread>128:
            raise ValueError(label+'：投影扩展超过当前支持范围')
        angle, distance = math.radians(e.angle), float(e.distance)
        self.warnings.append('简单投影采用近似合成，请预览核对边缘与 Photoshop 的差异。')
        return {'dx':-math.cos(angle)*distance, 'dy':math.sin(angle)*distance,
                'blur':float(e.size), 'spread':spread, 'opacity':float(e.opacity), 'color':rgb}

    def slot(self, layer, size, label):
        from psd_tools.constants import Tag
        if layer.kind != 'smartobject':
            raise ValueError(label + '：产品图层必须是智能对象')
        for tag in (Tag.FILTER_EFFECTS1,Tag.FILTER_EFFECTS2,Tag.FILTER_EFFECTS3):
            if tag in layer.tagged_blocks:
                raise ValueError(label + '：暂不支持产品层智能滤镜')
        data = next((layer.tagged_blocks.get_data(k).data for k in
                     (Tag.SMART_OBJECT_LAYER_DATA1,Tag.SMART_OBJECT_LAYER_DATA2) if k in layer.tagged_blocks), None)
        if data is None or b'Sz  ' not in data:
            raise ValueError(label + '：缺少智能对象原始尺寸')
        sw, sh = (float(data[b'Sz  '][k]) for k in (b'Wdth',b'Hght'))
        if min(sw,sh) <= 0 or max(sw,sh)>8192 or sw*sh>32_000_000:
            raise ValueError(label + '：智能对象画布过大或尺寸无效（当前上限 8192 边长、3200 万像素）')
        quad_values = data.get(b'nonAffineTransform',data.get(b'Trnf'))
        if quad_values is None or len(quad_values)!=8:
            raise ValueError(label + '：无法读取产品四角变换')
        quad = [[float(quad_values[i]),float(quad_values[i+1])] for i in range(0,8,2)]
        warp = data.get(b'warp')
        if warp:
            style = warp.get(b'warpStyle')
            style = getattr(style,'enum',None)
            if style not in (b'warpNone',b'warpCustom'):
                raise ValueError(label + '：暂不支持弯曲/曲面变形')
            for k in (b'warpValue',b'warpPerspective',b'warpPerspectiveOther'):
                if abs(float(warp.get(k,0)))>1e-6:
                    raise ValueError(label + '：暂不支持弯曲/曲面变形')
            custom = warp.get(b'customEnvelopeWarp')
            if custom:
                points = custom.get(b'meshPoints')
                if points is None or b'Hrzn' not in points or b'Vrtc' not in points:
                    raise ValueError(label + '：网格数据无法识别')
                xs,ys = list(points[b'Hrzn'].values),list(points[b'Vrtc'].values)
                bounds=warp.get(b'bounds')
                if not bounds:
                    raise ValueError(label + '：缺少变形网格边界')
                left,top,right,bottom=[float(bounds[k]) for k in (b'Left',b'Top ',b'Rght',b'Btom')]
                ex=[left+(right-left)*c/3 for r in range(4) for c in range(4)]
                ey=[top+(bottom-top)*r/3 for r in range(4) for c in range(4)]
                if len(xs)!=16 or len(ys)!=16 or not np.allclose(xs,ex,atol=1e-3) or not np.allclose(ys,ey,atol=1e-3):
                    raise ValueError(label + '：存在实际的网格弯曲，当前无法高速复现')
        native=(round(sw),round(sh))
        coefficients(quad,native)
        mask=self.mask(layer,size,label)
        slot={'kind':'slot','native_size':native,'quad':quad,'opacity':layer.opacity,'blend':self.blend_name(layer),
              'mask':self.asset(mask) if mask else None,'shadow':self.shadow(layer,label)}
        role=getattr(self,'artwork_roles',{}).get(id(layer))
        if role:slot['artwork_role']=role
        if b'quiltWarp' in data:
            from quilt_warp import parse_quilt,build_uv_map
            self.index+=1;filename=f'warp_{self.index:04d}.npz'
            self.progress(label+'：正在建立网格映射…')
            try:
                spec=parse_quilt(data,size)
                mapping=build_uv_map(spec,self.folder/filename,
                    progress=lambda done,total:self.progress(label+f'：网格 {done}/{total}'))
            except ValueError as exc:raise ValueError(label+'：该网格变形暂无法处理：'+str(exc)) from exc
            slot['warp']=filename
            self.warnings.append('网格变形使用缓存近似映射；请先核对图案位置、弯曲边缘和细节。')
        return slot

    def compile_layers(self, layers, size, label, force_visible=None):
        from psd_tools.constants import BlendMode
        ops=[]
        static=Image.new('RGBA',size)
        clip_stack=None
        clip_hidden=False
        def flush():
            nonlocal static
            if static.getbbox():
                ops.append({'kind':'static','asset':self.asset(static)})
                static=Image.new('RGBA',size)
        layers=list(layers)
        for index,layer in enumerate(layers):
            if layer is not force_visible and not layer.visible:
                if not layer.clipping:
                    clip_stack=None;clip_hidden=True
                continue
            here=label+'/'+layer.name
            if layer.clipping:
                if clip_hidden:continue
                if clip_stack is None or layer.is_group():
                    raise ValueError(here+'：当前只支持剪贴到普通位图/形状或产品智能对象基层，且基层不透明度需为100%')
                if layer.has_effects():
                    raise ValueError(here+'：剪贴层自身使用了图层样式，当前暂不支持')
            else:
                clip_stack=None;clip_hidden=False
            blend_if=self.check_layer(layer,here)
            if layer.kind=='brightnesscontrast':
                if layer.blend_mode!=BlendMode.NORMAL or layer.has_effects():
                    raise ValueError(here+'：亮度/对比度调整层目前只支持普通模式且无图层样式')
                from tone_adjustments import brightness_contrast_lut
                try:lut=brightness_contrast_lut(layer)
                except ValueError as exc:raise ValueError(here+'：'+str(exc)) from exc
                mask=self.mask(layer,size,here)
                op={'kind':'adjustment','adjustment':'brightnesscontrast','lut':lut,'opacity':layer.opacity,
                    'mask':self.asset(mask) if mask is not None else None}
                flush()
                if layer.clipping:clip_stack['ops'].append(op)
                else:ops.append(op)
                self.warnings.append('亮度/对比度使用查找表近似合成，请先核对颜色。')
            elif layer.is_group():
                if layer.has_effects():
                    raise ValueError(here+'：暂不支持组级图层样式')
                if layer.blend_mode == BlendMode.PASS_THROUGH:
                    if layer.opacity!=255 or (layer.has_mask() and not layer.mask.disabled) or (layer.has_vector_mask() and not layer.vector_mask.disabled):
                        raise ValueError(here+'：穿透组使用了透明度或蒙版，请改用普通组或简化副本')
                    children=self.compile_layers(list(layer),size,here)
                    flush();ops.extend(children)
                else:
                    children=self.compile_layers(list(layer),size,here)
                    mask=self.mask(layer,size,here)
                    flush();ops.append({'kind':'group','ops':children,'opacity':layer.opacity,'mask':self.asset(mask) if mask else None,
                                        'blend':self.blend_name(layer),'blend_if':blend_if})
            elif (id(layer) in self.target_layer_ids if hasattr(self,'target_layer_ids') else layer.name == self.product_name):
                flush()
                slot=self.slot(layer,size,here)
                slot['blend_if']=blend_if
                if layer.clipping:
                    clip_stack['ops'].append(slot)
                elif index+1<len(layers) and layers[index+1].clipping:
                    self.check_clip_base(layer,here,blend_if)
                    shadow=slot.pop('shadow',None)
                    # Blend the completed clipping group against the backdrop,
                    # rather than blending its base against transparent black.
                    blend=slot.pop('blend','normal')
                    clip_stack={'kind':'clipstack','base_op':slot,'ops':[],'shadow':shadow,'blend':blend}
                    ops.append(clip_stack)
                else:ops.append(slot)
            else:
                if layer.kind not in ('pixel','smartobject','type','shape'):
                    raise ValueError(here+'：暂不支持此类调整/填充图层（'+layer.kind+'），请在副本中预先栅格化')
                bitmap=layer.topil()
                if bitmap is None:
                    if layer.kind=='pixel' and (layer.width==0 or layer.height==0) and not layer.has_effects() and all(info.length==2 for info in layer._record.channel_info):
                        if not layer.clipping:clip_stack=None;clip_hidden=True
                        continue
                    raise ValueError(here+'：PSD 中没有可读取的图层像素，请在 Photoshop 中重新保存，或将固定层栅格化')
                full=Image.new('RGBA',size)
                full.alpha_composite(bitmap.convert('RGBA'),dest=(layer.left,layer.top))
                # Saved static layer pixels already contain the vector outline.
                # Keep that cache, including its antialiased edge, rather than
                # multiplying the same outline into its alpha a second time.
                mask=self.mask(layer,size,here,include_vector=False)
                if mask is not None and layer.kind=='shape' and layer.has_mask() and not layer.mask.has_real():
                    if ImageChops.difference(full.getchannel('A'),mask).getbbox() is None:
                        mask=None
                if mask:full.putalpha(ImageChops.multiply(full.getchannel('A'),mask))
                full=self.color_overlays(full,layer,here)
                shadow=self.shadow(layer,here,ignore_color_overlay=True)
                if shadow:shadow=dict(shadow,opacity=shadow['opacity']*layer.opacity/255)
                if layer.clipping:
                    clip_stack['ops'].append({'kind':'static','asset':self.asset(apply_opacity(full,layer.opacity)),
                                              'blend':self.blend_name(layer),'blend_if':blend_if})
                elif index+1<len(layers) and layers[index+1].clipping:
                    self.check_clip_base(layer,here,blend_if)
                    flush()
                    clip_stack={'kind':'clipstack','base':self.asset(full),'ops':[],'shadow':shadow,'blend':self.blend_name(layer)}
                    ops.append(clip_stack)
                elif layer.blend_mode in (BlendMode.MULTIPLY,BlendMode.SCREEN,BlendMode.SOFT_LIGHT,BlendMode.DARKEN) or blend_if:
                    flush()
                    if shadow:
                        ink=add_shadow(Image.new('RGBA',size),full,shadow)
                        if ink.getbbox():ops.append({'kind':'static','asset':self.asset(ink)})
                    ops.append({'kind':'static','asset':self.asset(apply_opacity(full,layer.opacity)),
                                'blend':self.blend_name(layer),'blend_if':blend_if})
                else:
                    static=add_shadow(static,full,shadow)
                    full=apply_opacity(full,layer.opacity)
                    static=Image.alpha_composite(static,full)
        flush()
        return ops


def slot_count(ops):
    return sum(1 if op['kind']=='slot' else
               slot_count(op['ops']) + (slot_count([op['base_op']]) if op.get('base_op') else 0)
               if op['kind'] in ('group','clipstack') else 0 for op in ops)


def has_product(layer,name):
    if layer.is_group():return any(has_product(child,name) for child in layer)
    return layer.name==name


def compile_psds(paths, cache_root, product_name='PRODUCT', progress=lambda text:None):
    from psd_tools import PSDImage
    from psd_tools.constants import ColorMode
    if not paths or not product_name.strip():raise ValueError('请选择 PSD 并填写需要替换的图层名')
    dest=unique_dir(cache_root,Path(paths[0]).stem+'_模板')
    compiler=Compiler(dest,product_name.strip())
    compiler.progress=progress
    scenes=[]
    try:
        for path in paths:
            progress('读取 '+Path(path).name)
            psd=PSDImage.open(path)
            if psd.color_mode != ColorMode.RGB or psd.depth!=8:
                raise ValueError(Path(path).name+'：请先在 Photoshop 副本中转换为 RGB、8 位再导入')
            w,h=psd.size
            if max(w,h)>8192 or w*h>32_000_000:raise ValueError('PSD 画布超过当前上限：8192 边长、3200 万像素')
            roots=list(psd)
            def all_layers(container):
                for item in container:
                    if item.is_group():yield from all_layers(item)
                    else:yield item
            from psd_tools.constants import Tag
            def source_id(item):
                if item.kind!='smartobject':return None
                for tag in (Tag.SMART_OBJECT_LAYER_DATA1,Tag.SMART_OBJECT_LAYER_DATA2):
                    if tag in item.tagged_blocks:
                        value=item.tagged_blocks.get_data(tag).data.get(b'Idnt')
                        return str(getattr(value,'value',value)).rstrip('\x00') if value is not None else None
            entries=list(all_layers(psd))
            target_ids={source_id(item) for item in entries if item.name==product_name.strip()}
            target_ids.discard(None)
            untouched=[item.name for item in entries if item.name!=product_name.strip() and source_id(item) in target_ids]
            if untouched:
                compiler.warnings.append('同源智能对象 '+', '.join(dict.fromkeys(untouched))+' 未命名为 '+product_name+'，将保留原内容；如需替换请改成相同图层名。')
            scene_groups=[layer for layer in roots if layer.is_group() and has_product(layer,product_name.strip())]
            targets=list(reversed(scene_groups)) if scene_groups else [None]
            for target in targets:
                name=Path(path).stem+('_'+target.name if target else '')
                active=[layer for layer in roots if layer not in scene_groups or layer is target]
                ops=compiler.compile_layers(active,psd.size,name,force_visible=target)
                count=slot_count(ops)
                if not count:raise ValueError(name+'：没有可见的 '+product_name+' 智能对象')
                scenes.append({'name':safe_name(name),'width':w,'height':h,'ops':ops,'slots':count})
            progress(Path(path).name+' 处理完成')
        data={'version':VERSION,'product_name':product_name,'sources':[str(Path(p).resolve()) for p in paths],
              'scenes':scenes,'warnings':list(dict.fromkeys(compiler.warnings)),
              'note':'固定图层使用 PSD 保存的栅格像素；下层灰度混合颜色带、亮度/对比度、投影及网格为近似。请先预览确认。'}
        write_json(dest/'template.json',data)
        return dest/'template.json'
    except Exception as exc:
        (dest/'导入失败.txt').write_text(str(exc),encoding='utf-8')
        raise


def load_image(path,preserve_alpha=False):
    with Image.open(path) as src:
        im=ImageOps.exif_transpose(src)
        alpha=im.convert('RGBA').getchannel('A')
        if not preserve_alpha and min(im.size)>2 and 224<=alpha.getextrema()[0]<255 and alpha.crop((1,1,im.width-1,im.height-1)).getextrema()==(255,255):
            alpha=Image.new('L',im.size,255)
        rgb=im.convert('RGB')
        if im.info.get('icc_profile'):
            try:
                rgb=ImageCms.profileToProfile(im.convert('CMYK') if im.mode=='CMYK' else rgb,
                    ImageCms.ImageCmsProfile(io.BytesIO(im.info['icc_profile'])),ImageCms.createProfile('sRGB'),outputMode='RGB')
            except Exception as exc:raise ValueError('无法读取输入图的颜色配置文件') from exc
        elif im.mode=='CMYK':raise ValueError('CMYK 产品图缺少颜色配置文件，请转换为 sRGB')
        rgb=rgb.convert('RGBA');rgb.putalpha(alpha)
        return rgb


def adjust_artwork_brightness(artwork, amount):
    """Lift or lower the midtones while preserving alpha and near-white padding."""
    white_edge = 249
    curve = []
    for value in range(256):
        position = min(value, white_edge) / white_edge
        curve.append(round(value + amount * 4 * position * (1 - position)))
    return artwork.point(curve * 3 + list(range(256)))


def auto_fit_info(im,size,policy=2):
    """Explain geometric placement for one artwork and one native print area."""
    alpha=im.getchannel('A') if im.mode=='RGBA' else im.convert('RGBA').getchannel('A')
    bounds=alpha.getbbox()
    if bounds is None:raise ValueError('印花图片完全透明，没有可放置的内容')
    has_alpha=alpha.getextrema()[0]<255
    source_size=(bounds[2]-bounds[0],bounds[3]-bounds[1]) if has_alpha else im.size
    ratio=(source_size[0]/source_size[1])/(size[0]/size[1])
    retained=min(ratio,1/ratio)
    if policy==1:
        mode='contain' if has_alpha or retained<0.92 else 'cover'
        reason=('透明图：去除外围透明留白，等比完整放入印区' if has_alpha else
                '比例接近：居中铺满，裁切不超过 8%' if mode=='cover' else
                '比例差异较大：等比完整保留画面')
    else:
        histogram=alpha.crop(bounds).histogram() if has_alpha else None
        rectangular=not has_alpha or sum(histogram[250:])/((bounds[2]-bounds[0])*(bounds[3]-bounds[1]))>=.98
        mode='cover' if rectangular else 'contain'
        reason=('满版图：等比铺满印区，比例不同时居中裁切' if rectangular else
                '透明图案：去除外围透明留白，等比完整放入印区')
    return dict(mode=mode,reason=reason,has_alpha=has_alpha,retained_fraction=retained,
                bounds=list(bounds) if has_alpha else None)


def prepare_cover_artwork(im):
    """V7 semantics: trim transparent margins and near-neutral white outer padding."""
    im=im.convert('RGBA') if im.mode!='RGBA' else im
    bounds=im.getchannel('A').getbbox()
    if bounds is None:raise ValueError('印花图片完全透明，没有可放置的内容')
    if bounds!=(0,0,*im.size):im=im.crop(bounds)
    corner=im.getpixel((0,0))
    if corner[3]<250 or min(corner[:3])<250 or max(corner[:3])-min(corner[:3])>2:return im
    left,top,right,bottom=im.width,im.height,0,0
    # Limit scratch buffers even for large source images; never turn white pixels transparent.
    for y in range(0,im.height,256):
        tile=np.asarray(im.crop((0,y,im.width,min(y+256,im.height))))
        rgb=tile[:,:,:3];low=rgb.min(axis=2);high=rgb.max(axis=2)
        content=(low<250)|((high-low)>2)|(tile[:,:,3]<250)
        rows=np.flatnonzero(content.any(axis=1));cols=np.flatnonzero(content.any(axis=0))
        if rows.size:
            top=min(top,y+int(rows[0]));bottom=max(bottom,y+int(rows[-1])+1)
            left=min(left,int(cols[0]));right=max(right,int(cols[-1])+1)
    if right>left and bottom>top and (left,top,right,bottom)!=(0,0,*im.size):return im.crop((left,top,right,bottom))
    return im


def cover_with_bleed(im,size):
    scale=max(size[0]/im.width,size[1]/im.height)*1.01
    width=max(size[0],math.ceil(im.width*scale));height=max(size[1],math.ceil(im.height*scale))
    left=(width-size[0])//2;top=(height-size[1])//2
    # Sample the centered crop directly; extreme panoramas must not allocate
    # a giant intermediate merely to discard almost all of it afterward.
    box=(left*im.width/width,top*im.height/height,
         (left+size[0])*im.width/width,(top+size[1])*im.height/height)
    return im.resize(size,Image.Resampling.BICUBIC,box=box)


def artwork_content_size(im):
    alpha=im.getchannel('A') if im.mode=='RGBA' else im.convert('RGBA').getchannel('A')
    bounds=alpha.getbbox()
    if bounds is None:raise ValueError('印花图片完全透明，没有可放置的内容')
    return bounds[2]-bounds[0],bounds[3]-bounds[1]


def rotation_for_size(source_size,target_size,orientation):
    if orientation not in ORIENTATIONS:raise ValueError('印花方向设置无效')
    if orientation!='auto':return {'original':0,'cw90':90,'ccw90':270,'rotate180':180}[orientation]
    source_ratio=source_size[0]/source_size[1];target_ratio=target_size[0]/target_size[1]
    # Near-square images/print areas have no reliable horizontal/vertical choice.
    opposite=(source_ratio>1.1 and target_ratio<1/1.1) or (source_ratio<1/1.1 and target_ratio>1.1)
    return 90 if opposite else 0


def combined_rotation(base,rotation):
    if isinstance(rotation,bool) or not isinstance(rotation,(int,float)) or not math.isfinite(rotation) or not -180<=rotation<=180:
        raise ValueError('印花角度请填写 -180～180 度的数字')
    angle=(base+rotation)%360
    return int(angle) if angle==int(angle) else angle


def orientation_info(im,size,orientation,rotation=0):
    source_size=artwork_content_size(im) if orientation=='auto' else im.size
    angle=combined_rotation(rotation_for_size(source_size,size,orientation),rotation)
    reason=(f'顺时针旋转{angle:g}°后适配' if 0<angle<=180 else f'逆时针旋转{360-angle:g}°后适配' if angle>180 else
            '横竖方向匹配，保持原方向' if orientation=='auto' and rotation==0 else '保持原方向')
    return dict(rotation=angle,reason=reason)


def rotate_artwork(im,angle):
    return im if angle==0 else im.transpose({90:Image.Transpose.ROTATE_270,270:Image.Transpose.ROTATE_90,180:Image.Transpose.ROTATE_180}[angle])


def fit_rotated_artwork(im,size,mode,angle,preserve_alpha=False,policy=2):
    """Rotate inside the native print plane; its outer geometry never changes."""
    if mode=='auto':
        info=auto_fit_info(im,size,policy=policy)
        if info['has_alpha']:im=im.crop(info['bounds'])
        mode=info['mode'];preserve_alpha=True
    radians=math.radians(angle);c=math.cos(radians);s=math.sin(radians)
    w,h=im.size;tw,th=size
    if mode in ('cover','cover_bleed'):
        scale=max((abs(c)*tw+abs(s)*th)/w,(abs(s)*tw+abs(c)*th)/h)
        if mode=='cover_bleed':scale*=1.01
        sx=sy=scale
    elif mode=='contain':
        sx=sy=min(tw/(abs(c)*w+abs(s)*h),th/(abs(s)*w+abs(c)*h))
    elif mode=='stretch':
        sx=tw/(abs(c)*w+abs(s)*h);sy=th/(abs(s)*w+abs(c)*h)
    else:raise ValueError('适配方式无效')
    a,b=c/sx,s/sy;d,e=-s/sx,c/sy
    matrix=(a,b,w/2-a*tw/2-b*th/2,d,e,h/2-d*tw/2-e*th/2)
    image=im if im.mode=='RGBA' else im.convert('RGBA')
    # AFFINE/BICUBIC samples a small fixed neighbourhood, so a large original
    # must be low-pass filtered before shrinking or fine details produce moire.
    # This intermediate only shrinks; keep the exact center via row scaling.
    # Row lengths of the inverse transform give the source-pixel footprint on
    # each source axis, including rotated, unequal stretch factors.
    reduction_x=min(1,1/math.hypot(a,b));reduction_y=min(1,1/math.hypot(d,e))
    if reduction_x<1 or reduction_y<1:
        reduced=(max(1,math.ceil(w*reduction_x)),max(1,math.ceil(h*reduction_y)))
        image=image.resize(reduced,Image.Resampling.LANCZOS)
        rx,ry=reduced[0]/w,reduced[1]/h
        matrix=tuple(value*(rx if index<3 else ry) for index,value in enumerate(matrix))
    fill=(255,255,255,255) if mode=='contain' and not preserve_alpha else (0,0,0,0)
    return image.transform(size,Image.Transform.AFFINE,matrix,Image.Resampling.BICUBIC,fillcolor=fill)


def fit_image(im,size,mode,preserve_alpha=False,policy=2,orientation='original',rotation=0):
    if mode=='cover_bleed':im=prepare_cover_artwork(im)
    angle=orientation_info(im,size,orientation,rotation=rotation)['rotation'] if orientation!='original' or rotation else 0
    if angle not in (0,90,180,270):return fit_rotated_artwork(im,size,mode,angle,preserve_alpha=preserve_alpha,policy=policy)
    if angle:im=rotate_artwork(im,angle)
    if mode=='cover_bleed':return cover_with_bleed(im,size)
    if mode=='auto':
        info=auto_fit_info(im,size,policy=policy)
        if info['has_alpha']:im=im.crop(info['bounds'])
        mode=info['mode']
        preserve_alpha=True
    if mode=='stretch':return im.resize(size,Image.Resampling.LANCZOS)
    if mode=='cover':return ImageOps.fit(im,size,Image.Resampling.LANCZOS,centering=(0.5,0.5))
    if mode!='contain':raise ValueError('适配方式无效')
    placed=ImageOps.contain(im,size,Image.Resampling.LANCZOS)
    canvas=Image.new('RGBA',size,(0,0,0,0) if preserve_alpha else 'white')
    canvas.paste(placed,((size[0]-placed.width)//2,(size[1]-placed.height)//2))
    return canvas


def placement_values(settings=None):
    settings=settings or {}
    values=[]
    for key,default,low,high in (('scale',100,10,300),('offset_x',0,-100,100),('offset_y',0,-100,100)):
        value=settings.get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'{key} 请填写 {low}～{high} 的数字')
        values.append(value)
    return tuple(values)


def prepare_placement_artwork(artwork,size,mode,fit_policy=2,orientation='original',rotation=0):
    """Return uncropped artwork and Canvas-style source-to-native affine geometry."""
    im=artwork.convert('RGBA') if artwork.mode!='RGBA' else artwork
    if mode=='cover_bleed':im=prepare_cover_artwork(im)
    angle=orientation_info(im,size,orientation,rotation)['rotation'] if orientation!='original' or rotation else 0
    if angle in (0,90,180,270):
        im=rotate_artwork(im,angle);angle=0
    preserve_alpha=mode=='auto'
    if mode=='auto':
        info=auto_fit_info(im,size,policy=fit_policy)
        if info['has_alpha']:im=im.crop(info['bounds'])
        mode=info['mode']
    w,h=im.size;tw,th=size
    c=math.cos(math.radians(angle));s=math.sin(math.radians(angle))
    if angle:
        if mode in ('cover','cover_bleed'):
            sx=sy=max((abs(c)*tw+abs(s)*th)/w,(abs(s)*tw+abs(c)*th)/h)*(1.01 if mode=='cover_bleed' else 1)
        elif mode=='contain':sx=sy=min(tw/(abs(c)*w+abs(s)*h),th/(abs(s)*w+abs(c)*h))
        elif mode=='stretch':sx=tw/(abs(c)*w+abs(s)*h);sy=th/(abs(s)*w+abs(c)*h)
        else:raise ValueError('适配方式无效')
        a,b,cc,d=sx*c,sy*s,-sx*s,sy*c
        x=tw/2-a*w/2-cc*h/2;y=th/2-b*w/2-d*h/2
    else:
        if mode=='cover_bleed':
            scale=max(tw/w,th/h)*1.01
            rw=max(tw,math.ceil(w*scale));rh=max(th,math.ceil(h*scale))
            x=-((rw-tw)//2);y=-((rh-th)//2)
        elif mode=='cover':
            scale=max(tw/w,th/h);rw=w*scale;rh=h*scale
            x=(tw-rw)/2;y=(th-rh)/2
        elif mode=='contain':
            # Match Pillow's integer contain geometry, without allocating it.
            rw,rh=w,h
            if w/h!=tw/th:
                if w/h>tw/th:rw=tw;rh=round(h/w*tw)
                else:rh=th;rw=round(w/h*th)
            else:rw,rh=tw,th
            rw=max(1,rw);rh=max(1,rh)
            x=(tw-rw)//2;y=(th-rh)//2
        elif mode=='stretch':rw,rh=tw,th;x=y=0
        else:raise ValueError('适配方式无效')
        a,b,cc,d=rw/w,0,0,rh/h
    corners=[(a*u+cc*v+x,b*u+d*v+y) for u,v in ((0,0),(w,0),(w,h),(0,h))]
    left=min(p[0] for p in corners);top=min(p[1] for p in corners)
    return im,dict(affine=[a,b,cc,d,x,y],x=left,y=top,
        width=max(p[0] for p in corners)-left,height=max(p[1] for p in corners)-top,
        rotation=angle,mode=mode,preserve_alpha=preserve_alpha)


def fit_placed_artwork(artwork,size,mode,placement,fit_policy=2,orientation='original',rotation=0,preserve_alpha=False):
    scale,dx,dy=placement_values(placement);scale/=100
    image,layout=prepare_placement_artwork(artwork,size,mode,fit_policy,orientation,rotation)
    a,b,c,d,x,y=layout['affine'];tw,th=size
    a*=scale;b*=scale;c*=scale;d*=scale
    x=tw/2+(x-tw/2)*scale+tw*dx/100
    y=th/2+(y-th/2)*scale+th*dy/100
    det=a*d-b*c
    matrix=(d/det,-c/det,(c*y-d*x)/det,-b/det,a/det,(b*x-a*y)/det)
    # Bound allocations to the input image and final native plane, even for
    # extreme panoramas or 300% placement. Low-pass before affine downsampling.
    ia,ib,_,id_,ie,_=matrix
    rx=min(1,1/math.hypot(ia,ib));ry=min(1,1/math.hypot(id_,ie))
    if rx<1 or ry<1:
        w,h=image.size
        reduced=(max(1,math.ceil(w*rx)),max(1,math.ceil(h*ry)))
        image=image.resize(reduced,Image.Resampling.LANCZOS)
        matrix=tuple(value*(reduced[0]/w if index<3 else reduced[1]/h) for index,value in enumerate(matrix))
    fill='white' if layout['mode']=='contain' and not (preserve_alpha or layout['preserve_alpha']) else (0,0,0,0)
    return image.transform(size,Image.Transform.AFFINE,matrix,Image.Resampling.BICUBIC,fillcolor=fill)


class Renderer:
    def __init__(self, manifest):
        self.path=Path(manifest)
        self.data=json.loads(self.path.read_text(encoding='utf-8'))
        if self.data.get('version') not in (1,2,3,VERSION) or not self.data.get('scenes'):raise ValueError('模板版本无效或没有场景')
        self.assets={}
        self.warp_maps={}
        self._fixed_images={}
        self._fixed_settings=None
        self._static_layers={}
        def walk(ops):
            for op in ops:
                for key in ('asset','mask','base'):
                    if op.get(key) and op[key] not in self.assets:
                        file=(self.path.parent/op[key]).resolve()
                        if file.parent!=self.path.parent.resolve():raise ValueError('模板素材路径无效')
                        with Image.open(file) as im:self.assets[op[key]]=im.copy()
                if op['kind'] in ('group','clipstack'):walk(op['ops'])
                if op.get('base_op'):walk([op['base_op']])
                if op['kind']=='slot':op['coeff']=coefficients(op['quad'],op['native_size'])
                if op.get('warp') and op['warp'] not in self.warp_maps:
                    from quilt_warp import load_map
                    file=(self.path.parent/op['warp']).resolve()
                    if file.parent!=self.path.parent.resolve():raise ValueError('网格素材路径无效')
                    self.warp_maps[op['warp']]=load_map(file)
                if op['kind']=='static':
                    layer=self.assets[op['asset']]
                    if op.get('mask'):
                        layer=layer.copy();layer.putalpha(ImageChops.multiply(layer.getchannel('A'),self.assets[op['mask']]))
                    shadow=op.get('shadow')
                    if shadow:shadow=dict(shadow,opacity=shadow['opacity']*op.get('opacity',255)/255)
                    self._static_layers[id(op)]=(apply_opacity(layer,op.get('opacity',255)),shadow_image(layer,shadow))
        for scene in self.data['scenes']:walk(scene['ops'])

    def render_scene(self,scene,artwork,mode,prepared,fit_policy=2,orientation='original',rotation=0,placement=None):
        size=(scene['width'],scene['height'])
        placed=placement_values(placement)!=(100,0,0)
        def compose(ops,initial=None,region=None):
            from blend_modes import composite_rgba
            local_size=(region[2]-region[0],region[3]-region[1]) if region else size
            canvas=initial.copy() if initial is not None else Image.new('RGBA',local_size)
            for op in ops:
                if op['kind']=='adjustment':
                    from tone_adjustments import apply_lut_rgba
                    mask=self.assets[op['mask']] if op.get('mask') else None
                    if mask is not None and region:mask=mask.crop(region)
                    canvas=apply_lut_rgba(canvas,op['lut'],
                        mask=mask,opacity=op.get('opacity',255))
                    continue
                elif op['kind']=='static':
                    layer=self._static_layers[id(op)][0]
                    if region:layer=layer.crop(region)
                elif op['kind']=='group':layer=compose(op['ops'])
                elif op['kind']=='clipstack':
                    base=compose([op['base_op']]) if op.get('base_op') else self.assets[op['base']].convert('RGBA')
                    bounds=base.getchannel('A').getbbox()
                    # Pixel-local clipped effects cannot affect the final image
                    # outside their base coverage. Unknown/nonlocal effects and
                    # legacy rounding paths keep the full-canvas implementation.
                    local=(self.data['version']>=4 and bounds is not None and bounds!=(0,0,*size)
                           and all(child['kind'] in ('static','adjustment') and not child.get('shadow') for child in op['ops']))
                    if local:
                        plane=base.crop(bounds);plane.putalpha(255)
                        patch=compose(op['ops'],initial=plane,region=bounds)
                        patch.putalpha(base.getchannel('A').crop(bounds))
                        layer=Image.new('RGBA',size);layer.paste(patch,bounds)
                    else:
                        plane=base.copy();plane.putalpha(255)
                        if self.data['version']<=3 and not op.get('base_op'):
                            # Preserve the original 8-bit rounding of existing caches.
                            layer=Image.alpha_composite(plane,compose(op['ops']))
                        else:
                            layer=compose(op['ops'],initial=plane)
                        layer.putalpha(base.getchannel('A'))
                elif op['kind']=='slot':
                    role=op.get('artwork_role')
                    slot_artwork=artwork[role] if isinstance(artwork,dict) else artwork
                    slot_prepared=prepared.setdefault(('role',role),{}) if isinstance(artwork,dict) else prepared
                    stage_size=tuple(op['native_size'])
                    if placed:
                        placement_key=('placement',stage_size,placement_values(placement),mode,fit_policy,orientation,rotation)
                        if placement_key not in slot_prepared:
                            slot_prepared[placement_key]=fit_placed_artwork(slot_artwork,stage_size,mode,placement,
                                fit_policy,orientation,rotation,preserve_alpha=bool(self.data.get('workbench')))
                        stage_size=placement_key
                    if stage_size not in slot_prepared:
                        working=slot_artwork
                        if mode=='cover_bleed':
                            if 'cover_artwork' not in slot_prepared:slot_prepared['cover_artwork']=prepare_cover_artwork(slot_artwork)
                            working=slot_prepared['cover_artwork']
                        angle=0
                        if orientation!='original' or rotation:
                            geometry=('orientation_size',id(working))
                            if geometry not in slot_prepared:slot_prepared[geometry]=artwork_content_size(working) if orientation=='auto' else working.size
                            angle=combined_rotation(rotation_for_size(slot_prepared[geometry],stage_size,orientation),rotation)
                            if angle in (0,90,180,270):
                                rotated=('orientation_image',id(working),angle)
                                if rotated not in slot_prepared:slot_prepared[rotated]=rotate_artwork(working,angle)
                                working=slot_prepared[rotated]
                        if angle not in (0,90,180,270):slot_prepared[stage_size]=fit_rotated_artwork(working,stage_size,mode,angle,preserve_alpha=bool(self.data.get('workbench')),policy=fit_policy)
                        elif mode=='cover_bleed':slot_prepared[stage_size]=cover_with_bleed(working,stage_size)
                        else:slot_prepared[stage_size]=fit_image(working,stage_size,mode,
                            preserve_alpha=bool(self.data.get('workbench')),policy=fit_policy)
                    if op.get('warp'):
                        from quilt_warp import render_rgba
                        layer=render_rgba(slot_prepared[stage_size],self.warp_maps[op['warp']])
                        if layer.size!=size:raise ValueError('网格映射尺寸与场景不一致，请重新导入 PSD')
                    else:
                        layer=slot_prepared[stage_size].transform(size,Image.Transform.PERSPECTIVE,op['coeff'],Image.Resampling.BICUBIC)
                else:raise ValueError('未知模板步骤')
                if op['kind']=='static':
                    ink=self._static_layers[id(op)][1]
                    if ink is not None:canvas=Image.alpha_composite(canvas,ink)
                else:
                    if op.get('mask'):
                        layer.putalpha(ImageChops.multiply(layer.getchannel('A'),self.assets[op['mask']]))
                    if op.get('shadow'):
                        shadow=dict(op['shadow'],opacity=op['shadow']['opacity']*op.get('opacity',255)/255)
                        canvas=add_shadow(canvas,layer,shadow)
                    layer=apply_opacity(layer,op.get('opacity',255))
                if op.get('blend_if'):
                    from blend_if import apply_blend_if
                    layer=apply_blend_if(canvas,layer,op['blend_if'])
                canvas=composite_rgba(canvas,layer,op.get('blend','normal'))
            return canvas
        return compose(scene['ops'])

    def render(self,path,settings,scene_indices=None):
        from artwork_sources import group_members,validate_roles
        members=group_members(path)
        if members:validate_roles(self.data)
        images={};prepared={}
        brightness = settings.get('brightness', 0)
        try:
            for role,item in (members or {'single':path}).items():
                artwork=load_image(item,preserve_alpha=settings['fit'] in ('auto','cover_bleed'))
                if settings.get('trim') and settings['fit']!='auto':
                    bounds=artwork.getchannel('A').getbbox()
                    if bounds:
                        cropped=artwork.crop(bounds);artwork.close();artwork=cropped
                if brightness:
                    adjusted = adjust_artwork_brightness(artwork, brightness)
                    artwork.close()
                    artwork = adjusted
                images[role]=artwork
            yield from self._render_images(images if members else images['single'],settings,prepared,scene_indices)
        finally:
            for image in images.values():image.close()

    def _render_images(self,artwork,settings,prepared,scene_indices=None):
        fixed_settings=(settings['fit'],settings['width'],settings['height'],settings['format'])
        if fixed_settings!=self._fixed_settings:
            self._fixed_images.clear()
            self._fixed_settings=fixed_settings
        for scene_index,scene in enumerate(self.data['scenes']):
            if scene_indices is not None and scene_index not in scene_indices:continue
            fixed=bool(self.data.get('workbench') and scene.get('fixed'))
            if fixed and scene_index in self._fixed_images:
                yield scene['name'],self._fixed_images[scene_index].copy()
                continue
            im=self.render_scene(scene,artwork,settings['fit'],prepared,fit_policy=settings.get('fit_policy',1),orientation=settings.get('orientation','original'),rotation=settings.get('rotation',0),placement=settings)
            target=(settings['width'],settings['height'])
            if target!=(0,0):
                fitted=ImageOps.contain(im,target,Image.Resampling.BICUBIC)
                output=Image.new('RGBA',target,(255,255,255,255) if settings['format']=='jpg' else (0,0,0,0))
                output.paste(fitted,((target[0]-fitted.width)//2,(target[1]-fitted.height)//2))
                im=output
            if settings['format']=='jpg':
                alpha=im.getchannel('A')
                if alpha.getextrema()==(255,255):im=im.convert('RGB')
                else:
                    white=Image.new('RGB',im.size,'white');white.paste(im,mask=alpha);im=white
            if fixed:self._fixed_images[scene_index]=im.copy()
            yield scene['name'],im


def settings_checked(settings):
    s=dict(settings)
    placement_values(s)
    for key in ('width','height','ppi','quality'):s[key]=int(s[key])
    if (s['width'],s['height'])!=(0,0) and (min(s['width'],s['height'])<16 or max(s['width'],s['height'])>8192 or s['width']*s['height']>32_000_000):
        raise ValueError('输出尺寸：两项都填 0 表示沿用 PSD，或填写 16～8192 的像素尺寸（不超过 3200 万像素）')
    if not 1<=s['ppi']<=2400 or not 1<=s['quality']<=100:raise ValueError('PPI 范围 1～2400；JPG 质量范围 1～100')
    if s['fit'] not in MODES or s['format'] not in ('jpg','png'):raise ValueError('输出设置无效')
    if 'orientation' in s and s['orientation'] not in ORIENTATIONS:raise ValueError('印花方向设置无效')
    if 'rotation' in s:combined_rotation(0,s['rotation'])
    if 'brightness' in s:
        value = s['brightness']
        if type(value) is not int or not -30 <= value <= 30:
            raise ValueError('印花亮度范围为 -30～30 的整数')
    if 'fit_policy' in s and (isinstance(s['fit_policy'],bool) or s['fit_policy'] not in (1,2)):raise ValueError('图片适配版本无效')
    if 'input_layout' in s and s['input_layout'] not in ('folders','flat'):raise ValueError('印花分组方式无效')
    if 'input_exclude' in s and (not isinstance(s['input_exclude'],list) or len(s['input_exclude'])>100 or any(not isinstance(x,str) or len(x)>100 for x in s['input_exclude'])):raise ValueError('排除关键词无效')
    if 'naming' in s:
        from export_naming import checked_naming
        s['naming']=checked_naming(s['naming'],safe_name)
    return s


class Control:
    def __init__(self):
        import threading
        self.stop=threading.Event()
        self.paused=threading.Event()
    def checkpoint(self):
        while self.paused.is_set() and not self.stop.is_set():time.sleep(0.1)
        return not self.stop.is_set()


def batch(manifest,input_folder,output_folder,settings,limit=0,resume=None,control=None,progress=lambda *args:None,workers=BATCH_WORKERS):
    from parallel_batch import run_batch
    return run_batch(sys.modules[__name__],manifest,input_folder,output_folder,settings,
                     limit=limit,resume=resume,control=control,progress=progress,workers=workers)
