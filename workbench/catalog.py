"""PSD inspection and scene rules. Source PSDs are never saved or renamed."""
import hashlib
import json
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from psd_tools import PSDImage
from psd_tools.constants import Tag, ColorMode
import engine
import compatibility


def natural(value):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', value)]


def fingerprint(path):
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('PSD 正在修改，请保存完成后重新导入')
    return {'bytes': after.st_size, 'mtime': after.st_mtime_ns, 'sha256': digest.hexdigest()}


def source_id(layer):
    for tag in (Tag.SMART_OBJECT_LAYER_DATA1, Tag.SMART_OBJECT_LAYER_DATA2):
        if tag in layer.tagged_blocks:
            value = layer.tagged_blocks.get_data(tag).data.get(b'Idnt')
            return str(getattr(value, 'value', value)).rstrip('\x00') if value is not None else None
    return None


def walk(container, prefix='', path=''):
    for index, layer in enumerate(container):
        ident = f'{prefix}.{index}' if prefix else str(index)
        label = path + '/' + layer.name
        yield ident, layer, label
        if layer.is_group():
            yield from walk(layer, ident, label)


def detect_targets(layers):
    mothers = [l for l in layers if l.get('root') and l['name'] == '母稿']
    if len(mothers) == 1 and mothers[0].get('source'):
        return {l['id'] for l in layers if l['id'] != mothers[0]['id'] and l.get('source') == mothers[0]['source']}
    if mothers:
        return set()
    named = [l for l in layers if l['name'].upper() == 'PRODUCT' or l['name'].upper().startswith('PRINT_')]
    sources = {l['source'] for l in named if l.get('source')}
    return {l['id'] for l in layers if l in named or (l.get('source') and l['source'] in sources)}


def scene_nodes(psd):
    groups = [(i, l) for i, l in enumerate(psd) if l.is_group()]
    numbered = [(str(i), l) for i, l in groups if re.fullmatch(r'\d+', l.name.strip())]
    if numbered:
        return sorted(numbered, key=lambda item: natural(item[1].name.strip()))
    if groups:
        smart = [{'id':i, 'name':l.name, 'source':source_id(l), 'root':'.' not in i}
                 for i,l,_ in walk(psd) if l.kind == 'smartobject']
        targets = detect_targets(smart)
        actual = [(str(i), l) for i,l in groups if any(t.startswith(str(i)+'.') for t in targets)]
        if actual:
            return actual
        # Unknown structure: a single document is safer than hiding common backgrounds.
    return [('document', psd)]


def scene_image(psd, scene, scenes):
    # Use the same checked compositor as export. The vendor composite silently
    # ignores several adjustment types, which used to produce misleading thumbs.
    with tempfile.TemporaryDirectory(prefix='taotu-thumbnail-') as folder:
        compiler = engine.Compiler(folder, '__WORKBENCH_PRINT__')
        sid = next(ident for ident, group in scenes if group is scene)
        ops = compile_scene(psd, {'id':sid,'name':str(sid),'mode':'fixed','targets':[]}, compiler)
        return render_static(compiler, ops, psd.size)


def render_static(compiler, ops, size):
    path = compiler.folder / '_static.json'
    scene = {'name':'static','width':size[0],'height':size[1],'ops':ops}
    try:
        engine.write_json(path, {'version':engine.VERSION,'scenes':[scene]})
        renderer = engine.Renderer(path)
        return renderer.render_scene(renderer.data['scenes'][0], None, 'stretch', {})
    finally:
        path.unlink(missing_ok=True)


def inspect(path, media_root, progress=lambda message: None, previous=None):
    path = Path(path).resolve()
    if path.suffix.lower() not in ('.psd', '.psb') or not path.is_file():
        raise ValueError('请选择 PSD 或 PSB 文件')
    signature = fingerprint(path)
    psd = PSDImage.open(path)
    if psd.color_mode != ColorMode.RGB or psd.depth != 8:
        raise ValueError('请先将 PSD 转为 RGB、8 位')
    if max(psd.size) > 8192 or psd.width * psd.height > 32_000_000:
        raise ValueError('PSD 超过 8192 边长或 3200 万像素上限')
    ident = hashlib.sha256(str(path).casefold().encode()).hexdigest()[:20]
    thumb_dir = Path(media_root) / ident
    thumb_dir.mkdir(parents=True, exist_ok=True)
    layers = list(walk(psd))
    smart = [{'id': i, 'name': l.name, 'source': source_id(l), 'root': '.' not in i,
              'path': label, 'visible': l.visible} for i, l, label in layers if l.kind == 'smartobject']
    detected = detect_targets(smart)
    scenes = scene_nodes(psd)
    records, warnings = [], []
    if any(l.is_group() for l in psd) and not any(re.fullmatch(r'\d+', l.name.strip()) for l in psd if l.is_group()):
        warnings.append('未找到数字场景组。当前按含印花的组识别，其他组作为公共内容；若有独立介绍页，请把场景组命名为 1、2、3 后重新导入。')
    if not detected:
        warnings.append('没有认准印花层。请展开场景，手动选择需要换图的智能对象。')
    for i, (sid, group) in enumerate(scenes):
        progress(f'读取 {path.stem} · 场景 {i + 1}/{len(scenes)}')
        candidates = [dict(s, linked=s['id'] in detected) for s in smart
                      if (sid == 'document' or s['id'].startswith(sid + '.'))]
        targets = [s['id'] for s in candidates if s['id'] in detected]
        thumb = None
        try:
            image = scene_image(psd, group, scenes)
            image = ImageOps.contain(image, (360, 360))
            image.save(thumb_dir / f'{i}.png')
            thumb = f'/media/thumbs/{ident}/{i}.png'
        except Exception as exc:
            warnings.append(f'场景 {group.name if sid != "document" else "1"} 缩略图未生成：{exc}')
        records.append({'id': sid, 'name': group.name.strip() if sid != 'document' else '1',
                        'mode': 'replace' if targets else 'fixed', 'targets': targets,
                        'candidates': candidates, 'thumbnail': thumb})
    if previous and previous['digest'] == signature['sha256']:
        by_id = {s['id']:s for s in previous['scenes']}
        for scene in records:
            old = by_id.get(scene['id'])
            if old:
                scene.update({k:old[k] for k in ('mode','targets','artwork_roles') if k in old})
        order = {s['id']:i for i,s in enumerate(previous['scenes'])}
        records.sort(key=lambda scene:order.get(scene['id'],len(order)))
    item = {'id': ident, 'name': path.stem, 'path': str(path), 'signature': signature,
            'digest': signature['sha256'], 'canvas': list(psd.size), 'scenes': records, 'warnings': warnings}
    item['compatibility'] = audit(psd, item, progress)
    return item


def updated_scenes(template, submitted):
    originals = {s['id']: s for s in template['scenes']}
    if len(submitted) != len(originals) or {s['id'] for s in submitted} != set(originals):
        raise ValueError('场景列表不完整，请重新读取模板')
    result = []
    for scene in submitted:
        original = originals[scene['id']]
        mode = scene.get('mode')
        targets = list(dict.fromkeys(scene.get('targets', [])))
        if mode not in ('replace', 'fixed', 'skip'):
            raise ValueError('场景处理方式无效')
        if not set(targets) <= {l['id'] for l in original['candidates']}:
            raise ValueError('选择了不属于这个场景的图层')
        if mode == 'replace' and not targets:
            raise ValueError(f'场景 {original["name"]} 需要选择至少一个印花层')
        roles=scene.get('artwork_roles',original.get('artwork_roles',{}))
        if not isinstance(roles,dict) or any(key not in {l['id'] for l in original['candidates']} or value not in ('left','center','right') for key,value in roles.items()):
            raise ValueError('印花图层对应关系无效')
        result.append(dict(original, mode=mode, targets=targets,artwork_roles=dict(roles)))
    if all(s['mode'] == 'skip' for s in result):
        raise ValueError('至少保留一个导出场景')
    return result


def compile_template(template, folder, progress=lambda message: None, report=None):
    path = Path(template['path'])
    if fingerprint(path)['sha256'] != template['digest']:
        raise ValueError('PSD 已经修改，请重新导入并核对场景设置')
    psd = PSDImage.open(path)
    compiler = engine.Compiler(folder, '__WORKBENCH_PRINT__')
    compiler.progress = progress
    compiled, issues, features = [], [], {}
    for scene in template['scenes']:
        if scene['mode'] == 'skip':
            continue
        progress(f'建立场景 {scene["name"]} 的缓存')
        issues.extend(layer_issues(psd, scene, compiler, features))
        try:
            ops = compile_scene(psd, scene, compiler)
        except ValueError as exc:
            add_issue(issues, scene, str(exc))
            continue
        ops = freeze_static_prefixes(compiler, ops, psd.size)
        compiled.append({'name': scene['name'], 'width': psd.width, 'height': psd.height,
                         'slots': engine.slot_count(ops), 'ops': ops, 'fixed': scene['mode'] == 'fixed'})
    value = compatibility.report(template, issues, compiler.warnings, features)
    if report: report(value)
    compatibility.require(dict(template, compatibility=value))
    if not compiled:
        raise ValueError('没有需要导出的场景')
    engine.write_json(Path(folder) / 'template.json', {'version': 4, 'workbench': True,
        'product_name': '__WORKBENCH_PRINT__', 'sources': [str(path)], 'scenes': compiled,
        'warnings': list(dict.fromkeys(compiler.warnings)), 'source_digest': template['digest']})


def compile_scene(psd, scene, compiler):
    nodes = dict(scene_nodes(psd))
    group = nodes[scene['id']]
    fixed = scene['mode'] == 'fixed'
    if not fixed and not scene['targets']:
        raise ValueError(f'场景 {scene["name"]}：没有选择印花层')
    lookup = {i: layer for i, layer, _ in walk(psd)}
    compiler.target_layer_ids = set() if fixed else {id(lookup[i]) for i in scene['targets']}
    compiler.artwork_roles = {id(lookup[i]): role for i, role in scene.get('artwork_roles', {}).items()}
    groups = set(nodes.values()) - {psd}
    active = [layer for layer in psd if layer not in groups or layer is group]
    ops = compiler.compile_layers(active, psd.size, scene['name'], force_visible=group if group is not psd else None)
    if not fixed and not engine.slot_count(ops):
        raise ValueError(f'场景 {scene["name"]} 选中的印花不可见，请检查图层开关')
    return ops


def freeze_static_prefixes(compiler, ops, size):
    """Bake only prefixes with no artwork/backdrop dependency; keep slot ops live."""
    for op in ops:
        if op['kind'] == 'group':
            op['ops'] = freeze_static_prefixes(compiler, op['ops'], size)
    end = next((i for i,op in enumerate(ops) if engine.slot_count([op])), len(ops))
    if end and (end > 1 or ops[0]['kind'] != 'static'):
        image = render_static(compiler, ops[:end], size)
        return [{'kind':'static','asset':compiler.asset(image)}] + ops[end:]
    return ops


def add_issue(issues, scene, message, layer=None):
    if not any(i['scene'] == scene['id'] and i['message'] == message for i in issues):
        issues.append({'scene':scene['id'],'scene_name':scene['name'],'layer':layer,'message':message})


def layer_issues(psd, scene, compiler, features):
    """Check all active layers with the compiler's parameter validators."""
    if psd is None: return []
    from psd_tools.constants import BlendMode
    from psd_adjustments import SUPPORTED_KINDS, compile_adjustment
    from tone_adjustments import brightness_contrast_lut
    nodes = dict(scene_nodes(psd)); group = nodes[scene['id']]
    groups = set(nodes.values()) - {psd}
    active = [layer for layer in psd if layer not in groups or layer is group]
    lookup = {ident:layer for ident,layer,_ in walk(psd)}
    targets = {id(lookup[ident]) for ident in scene['targets']} if scene['mode']=='replace' else set()
    issues = []
    def visit(layers, prefix, force=None):
        layers=list(layers);hidden_base=False;have_base=False
        for index,layer in enumerate(layers):
            if not layer.visible and layer is not force:
                if not layer.clipping:hidden_base=True;have_base=False
                continue
            if layer.clipping and hidden_base:continue
            if not layer.clipping:hidden_base=False
            label=prefix+'/'+layer.name
            feature=layer.kind+':'+layer.blend_mode.name.lower()
            features[feature]=features.get(feature,0)+1
            def check(fn):
                try:return fn()
                except ValueError as exc:
                    message=str(exc)
                    if not message.startswith(label):message=label+'：'+message
                    add_issue(issues,scene,message,label)
            def fail(message):raise ValueError(message)
            blend_if=check(lambda:compiler.check_layer(layer,label))
            if layer.clipping:
                if not have_base or layer.is_group():check(lambda:fail('当前只支持剪贴到普通位图/形状或产品智能对象基层，且基层不透明度需为100%'))
                if layer.has_effects():check(lambda:fail('剪贴层自身使用了图层样式，当前暂不支持'))
            else:have_base=False
            # Match compile_layers: empty pixel bases suppress the entire clip chain.
            empty=(layer.kind=='pixel' and (layer.width==0 or layer.height==0)
                   and not layer.has_effects() and all(info.length==2 for info in layer._record.channel_info)
                   and layer.topil() is None)
            if empty:
                if not layer.clipping:hidden_base=True
                continue
            dynamic=id(layer) in targets
            adjustment=layer.kind in SUPPORTED_KINDS or layer.kind=='brightnesscontrast'
            if adjustment:
                if layer.blend_mode!=BlendMode.NORMAL or layer.has_effects():check(lambda:fail('调整层目前只支持普通模式且无图层样式'))
                check(lambda:compile_adjustment(layer) if layer.kind in SUPPORTED_KINDS else brightness_contrast_lut(layer))
                check(lambda:compiler.mask(layer,psd.size,label))
            elif layer.is_group():
                if layer.has_effects():check(lambda:fail('暂不支持组级图层样式'))
                if layer.blend_mode==BlendMode.PASS_THROUGH:
                    if layer.opacity!=255 or (layer.has_mask() and not layer.mask.disabled) or (layer.has_vector_mask() and not layer.vector_mask.disabled):
                        check(lambda:fail('穿透组使用了透明度或蒙版，请改用普通组或简化副本'))
                else:check(lambda:compiler.mask(layer,psd.size,label))
                visit(layer,label)
            elif dynamic:
                check(lambda:compiler.slot(layer,psd.size,label,validate_only=True))
            elif layer.kind in ('pixel','smartobject','type','shape'):
                check(lambda:compiler.mask(layer,psd.size,label,include_vector=False))
                if layer.has_effects():
                    check(lambda:compiler.color_overlays(Image.new('RGBA',(1,1)),layer,label))
                    check(lambda:compiler.shadow(layer,label,ignore_color_overlay=True))
            else:check(lambda:fail('暂不支持此类调整/填充图层（'+layer.kind+'），请在副本中预先栅格化'))
            if not layer.clipping and not layer.is_group() and not adjustment and index+1<len(layers) and layers[index+1].clipping:
                have_base=True
                check(lambda:compiler.check_clip_base(layer,label,blend_if))
    visit(active,scene['name'],group if group is not psd else None)
    return issues


def audit(psd, template, progress=lambda message: None):
    issues, warnings, features = [], [], {}
    for scene in template['scenes']:
        if scene['mode'] == 'skip': continue
        progress(f'检查场景 {scene["name"]} 的套图兼容性')
        with tempfile.TemporaryDirectory(prefix='taotu-preflight-') as folder:
            compiler = engine.Compiler(folder,'__WORKBENCH_PRINT__');compiler.progress = progress
            issues.extend(layer_issues(psd,scene,compiler,features))
            try: compile_scene(psd,scene,compiler)
            except ValueError as exc: add_issue(issues,scene,str(exc))
            warnings.extend(compiler.warnings)
    return compatibility.report(template,issues,warnings,features)


def preflight(psd, scenes, progress=lambda message: None):
    """Exercise the actual compiler once per replace scene; never save the PSD."""
    warnings = []
    for scene in scenes:
        if scene['mode'] != 'replace':
            continue
        progress(f'检查场景 {scene["name"]} 的套图兼容性')
        # Release scratch assets after each scene to bound disk usage.
        with tempfile.TemporaryDirectory(prefix='taotu-preflight-') as folder:
            compiler = engine.Compiler(folder, '__WORKBENCH_PRINT__')
            compiler.progress = progress
            try:
                compile_scene(psd, scene, compiler)
                warnings.extend(compiler.warnings)
            except ValueError as exc:
                warnings.append(f'套图兼容提醒：{exc}')
    return list(dict.fromkeys(warnings))
