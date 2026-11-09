"""Single-task application service. PSDs, artworks and outputs stay on this PC."""
import copy
import gc
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import engine
import catalog
from cache import DiskCache

DEFAULTS = {'input': '', 'output': '', 'settings': {'width': 0, 'height': 0,
            'ppi': 72, 'quality': 100, 'fit': 'auto', 'format': 'jpg', 'trim': False, 'fit_policy': 2, 'orientation': 'auto', 'rotation': 0, 'brightness': 0,
            'scale': 100, 'offset_x': 0, 'offset_y': 0,
            'naming': {'folder': '{name}', 'image': '{scene:02}', 'start': 1}},
            'limit': 0, 'preferences_version': 2}


class Service:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ('thumbs', 'previews', 'temp', 'artworks'):
            (self.root / name).mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.templates = self._read('templates.json', [])
        self.preferences = self._read('preferences.json', copy.deepcopy(DEFAULTS))
        if self.preferences.get('preferences_version', 0)<2:
            old=self.preferences.get('settings', {})
            if (old.get('width'),old.get('height'),old.get('fit'))==(1254,1254,'contain'):
                old.update(width=0,height=0,fit='auto')
            self.preferences['preferences_version']=2
            self.save('preferences.json',self.preferences)
        self.history = self._read('history.json', [])
        self.cache = DiskCache(self.root / 'cache', self._read('cache-settings.json', {}).get('limit_bytes', 10 * 1024**3))
        self.task = {'status': 'idle', 'kind': '', 'message': '选择模板，开始套图。', 'progress': 0,
                     'total': 0, 'results': [], 'preview': [], 'error': ''}
        self.control = None

    def _read(self, name, default):
        try:
            return json.loads((self.root / name).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default

    def save(self, name, value):
        engine.write_json(self.root / name, value)

    def state(self):
        with self.lock:
            result = copy.deepcopy({'templates': self.templates, 'preferences': self.preferences,
                                    'history': self.history, 'task': self.task})
        for template in result['templates']:
            try:
                stat = Path(template['path']).stat()
                template['stale'] = (stat.st_size, stat.st_mtime_ns) != (template['signature']['bytes'], template['signature']['mtime'])
            except OSError:
                template['stale'] = True
        result['cache'] = self.cache.summary()
        return result

    def update(self, **values):
        with self.lock:
            self.task.update(values)

    def idle_required(self):
        if self.task['status'] in ('running', 'paused', 'stopping'):
            raise ValueError('请先等待当前任务完成，或停止并保存进度')

    def start(self, kind, fn):
        with self.lock:
            self.idle_required()
            self.control = engine.Control()
            task_id=uuid.uuid4().hex
            self.task.update(task_id=task_id,status='running', kind=kind, message='正在准备…', progress=0, total=0,
                             error='', results=[])
        def work():
            final_status = 'done'
            message = '处理完成。'
            error = ''
            try:
                fn()
                stopped = self.control and self.control.stop.is_set()
                message = '已停止并保存进度。' if stopped else '处理完成。'
            except Exception as exc:
                final_status = 'failed'
                error = message = str(exc)
            finally:
                gc.collect()
                with self.lock:
                    try:
                        self.finish_history()
                    except Exception as exc:
                        final_status = 'failed'
                        error = (error + '\n' if error else '') + '批次记录保存失败：' + str(exc)
                        message = error
                    finally:
                        self.control = None
                        self.task.update(status=final_status, error=error, message=message)
        threading.Thread(target=work, daemon=True, name='workbench-task').start()
        return {'ok': True, 'task_id': task_id}

    def finish_history(self):
        for entry in self.history:
            if entry.get('status') != '运行中':
                continue
            try:
                saved = json.loads((Path(entry['path']) / '运行状态.json').read_text(encoding='utf-8'))
                entry['status'] = saved.get('status', '处理中断')
                if entry['status'] == '运行中':
                    entry['status'] = '处理中断'
                records = list(saved.get('records', {}).values())
                entry['counts'] = {'success': sum(r['status'] == '成功' for r in records),
                                   'failed': sum(r['status'] == '失败' for r in records)}
            except (OSError, ValueError, KeyError, TypeError):
                entry['status'] = '处理中断'
        if self.history:
            self.save('history.json', self.history)

    def find(self, ident):
        return next((t for t in self.templates if t['id'] == ident), None) or self._missing()

    @staticmethod
    def _missing():
        raise ValueError('模板不存在，请重新选择')

    def scan(self, paths):
        if not paths:
            raise ValueError('没有选择 PSD')
        def work():
            failures = []
            for i, path in enumerate(dict.fromkeys(paths)):
                self.update(progress=i, total=len(paths), message='正在读取 ' + Path(path).name)
                try:
                    item = catalog.inspect(path, self.root / 'thumbs', lambda message: self.update(message=message))
                    with self.lock:
                        old = next((t for t in self.templates if t['id'] == item['id']), None)
                        if old and old['digest'] == item['digest']:
                            # Reimporting an unchanged source preserves user choices.
                            by_id = {s['id']: s for s in old['scenes']}
                            for scene in item['scenes']:
                                previous = by_id.get(scene['id'])
                                if previous:
                                    scene.update(mode=previous['mode'], targets=previous['targets'])
                            order = {scene['id']: index for index, scene in enumerate(old['scenes'])}
                            item['scenes'].sort(key=lambda scene: order.get(scene['id'], len(order)))
                        self.templates = [t for t in self.templates if t['id'] != item['id']] + [item]
                        self.save('templates.json', self.templates)
                except Exception as exc:
                    failures.append(Path(path).name + '：' + str(exc))
                if self.control.stop.is_set():
                    break
            self.update(progress=i + 1, total=len(paths))
            if failures:
                raise ValueError('部分模板未导入：\n' + '\n'.join(failures))
        return self.start('scan', work)

    def configure(self, ident, scenes):
        with self.lock:
            self.idle_required()
            template = self.find(ident)
            template['scenes'] = catalog.updated_scenes(template, scenes)
            self.save('templates.json', self.templates)
        return {'ok': True}

    def manifest(self, template):
        if catalog.fingerprint(template['path'])['sha256'] != template['digest']:
            raise ValueError('PSD 已修改，请重新导入并核对场景设置')
        return self.cache.ensure(template, lambda folder: catalog.compile_template(
            template, folder, lambda message: self.update(message=message)))

    @staticmethod
    def input_images(folder, exclude=None):
        if not str(folder).strip():
            raise ValueError('请选择存在的印花文件夹')
        root = Path(folder)
        if not root.is_dir():
            raise ValueError('请选择存在的印花文件夹')
        from artwork_sources import discover
        return discover(root,engine.EXTENSIONS,exclude=exclude)

    def artworks(self, folder, offset=0, limit=40, exclude=None):
        offset=int(offset);limit=min(100,max(1,int(limit)))
        if offset<0:raise ValueError('图片分页位置不能为负数')
        images=self.input_images(folder,exclude)
        files=[]
        from artwork_sources import thumbnail as source_thumbnail, signature as source_signature, source_key
        cache=self.root/'artworks'
        for path in images[offset:offset+limit]:
            key=hashlib.sha256((str(path.resolve())+repr(source_signature(path))).encode('utf-8')).hexdigest()
            label=source_key(path,Path(folder))
            kind='triptych' if path.is_dir() else 'image'
            thumbnail=cache/(key+'.png')
            try:
                with source_thumbnail(path) as image:
                    width,height=image.size
                    has_alpha=image.getchannel('A').getextrema()[0]<255
                    if not thumbnail.is_file():
                        image.thumbnail((220,220))
                        image.save(thumbnail)
                    else:thumbnail.touch()
                files.append(dict(path=str(path.resolve()),name=label,kind=kind,url='/media/artworks/'+thumbnail.name,
                                  width=width,height=height,has_alpha=has_alpha))
            except (OSError,ValueError) as exc:
                files.append(dict(path=str(path.resolve()),name=label,kind=kind,url='',width=0,height=0,
                                  has_alpha=False,error='图片无法读取：'+str(exc)))
        cached=sorted((p for p in cache.glob('*.png') if not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in cached[512:]:
            if path.resolve().parent==cache.resolve() and len(path.stem)==64:
                path.unlink(missing_ok=True)
        included=set(images)
        excluded=[source_key(p,Path(folder)) for p in self.input_images(folder,[]) if p not in included]
        return dict(folder=str(Path(folder).resolve()),total=len(images),offset=offset,files=files,excluded_count=len(excluded),excluded=excluded[:100])

    @staticmethod
    def output_directory(value, input_folder=''):
        if not str(value).strip():raise ValueError('请选择输出文件夹')
        output=Path(value).resolve()
        if str(input_folder).strip() and (output==Path(input_folder).resolve() or Path(input_folder).resolve() in output.parents):
            raise ValueError('输出文件夹必须与印花文件夹分开，不能放在印花文件夹内部')
        # Windows reserved names and invalid components must fail before mkdir.
        for part in output.parts[1:]:
            if engine.safe_name(part)!=part or part.endswith((' ','.')):
                raise ValueError('输出文件夹名称无效：'+part)
        return output

    def save_preferences(self, payload):
        with self.lock:
            self.idle_required()
            settings=engine.settings_checked(payload.get('settings',self.preferences['settings']))
            input_folder=str(payload.get('input',self.preferences.get('input',''))).strip()
            output_folder=str(payload.get('output',self.preferences.get('output',''))).strip()
            if input_folder and not Path(input_folder).is_dir():raise ValueError('请选择存在的印花文件夹')
            limit=int(payload.get('limit',self.preferences.get('limit',0)))
            if limit<0:raise ValueError('处理数量不能为负数')
            if output_folder:
                output=self.output_directory(output_folder,input_folder)
                output.mkdir(parents=True,exist_ok=True)
                output_folder=str(output)
            self.preferences=dict(input=input_folder,output=output_folder,settings=settings,limit=limit,preferences_version=2)
            self.save('preferences.json',self.preferences)
        return {'ok':True}

    def open_output(self, path):
        if not str(path).strip():raise ValueError('请选择输出文件夹')
        target=Path(path).resolve()
        with self.lock:
            recorded=[self.preferences.get('output','')]
            recorded += [entry.get(key,'') for entry in self.history for key in ('path','output')]
        allowed={Path(value).resolve() for value in recorded if value}
        if target not in allowed or not target.is_dir():raise ValueError('只能打开当前输出文件夹或本机批次记录中的文件夹')
        os.startfile(str(target))
        return {'ok':True}

    @staticmethod
    def checked_scene(template, scene):
        scenes = [s for s in template['scenes'] if s.get('mode') != 'skip']
        if scene is not None and (type(scene) is not int or not 0 <= scene < len(scenes)):
            raise ValueError('请选择有效的轮播场景')
        return scenes

    def editor(self, ident, source, settings, scene=0):
        template = copy.deepcopy(self.find(ident))
        settings = engine.settings_checked(settings)
        self.checked_scene(template, scene)
        if scene is None:raise ValueError('请选择需要编辑的场景')
        requested_source = str(source)
        source = Path(source)
        if source.is_dir():
            images = self.input_images(source, settings.get('input_exclude'))
            if not images:raise ValueError('印花文件夹里没有支持的图片')
            source = images[0]
        if not source.is_file():raise ValueError('请先选择一张印花图片')
        def work():
            from PIL import ImageOps, Image
            manifest = self.manifest(template)
            with self.cache.use(manifest):
                data = json.loads(manifest.read_text(encoding='utf-8'))
                def slots(ops):
                    for op in ops:
                        if op['kind']=='slot':yield op
                        if op.get('ops'):yield from slots(op['ops'])
                        if op.get('base_op'):yield from slots([op['base_op']])
                slot = next(slots(data['scenes'][scene]['ops']), None)
                if slot is None:raise ValueError('这个场景是固定图，没有可调整的印花层，请选择换图场景')
                stage = tuple(slot['native_size'])
                artwork = engine.load_image(source,preserve_alpha=settings['fit'] in ('auto','cover_bleed'))
                try:
                    if settings.get('trim') and settings['fit']!='auto':
                        bounds=artwork.getchannel('A').getbbox()
                        if bounds:
                            cropped=artwork.crop(bounds);artwork.close();artwork=cropped
                    if settings.get('brightness',0):
                        adjusted=engine.adjust_artwork_brightness(artwork,settings['brightness'])
                        artwork.close();artwork=adjusted
                    prepared, layout = engine.prepare_placement_artwork(artwork,stage,settings['fit'],settings.get('fit_policy',1),settings.get('orientation','original'),settings.get('rotation',0))
                    try:
                        thumb=prepared.copy();thumb.thumbnail((1600,1600),Image.Resampling.LANCZOS)
                        affine=list(layout['affine'])
                        for index in (0,1):affine[index]*=prepared.width/thumb.width
                        for index in (2,3):affine[index]*=prepared.height/thumb.height
                        folder=self.root/'artworks'/'editor';folder.mkdir(exist_ok=True)
                        filename=uuid.uuid4().hex+'.png';thumb.save(folder/filename);thumb.close()
                        self.update(editor=dict(layout,affine=affine,url='/media/artworks/editor/'+filename,
                            stage_width=stage[0],stage_height=stage[1],scene=scene,source=requested_source,
                            template_id=ident,settings=settings),progress=1,total=1)
                        for old in sorted(folder.glob('*.png'),key=lambda p:p.stat().st_mtime,reverse=True)[16:]:
                            if not old.is_symlink() and old.resolve().parent==folder.resolve():old.unlink()
                    finally:
                        if prepared is not artwork:prepared.close()
                finally:artwork.close()
        return self.start('editor',work)

    def preview(self, ident, source, settings, scene=None):
        template = copy.deepcopy(self.find(ident))
        settings = engine.settings_checked(settings)
        self.checked_scene(template,scene)
        requested_source = str(source)
        source = Path(source)
        if source.is_dir():
            images = self.input_images(source,settings.get('input_exclude'))
            if not images:
                raise ValueError('印花文件夹里没有支持的图片')
            source = images[0]
        if not source.is_file() and not source.is_dir():
            raise ValueError('请选择一张印花图片或印花文件夹')
        def work():
            manifest = self.manifest(template)
            from artwork_sources import signature as artwork_signature
            preview_stamp=list(artwork_signature(source))
            with self.cache.use(manifest):
                renderer = engine.Renderer(manifest)
                fit_info=[]
                native_sizes=set()
                def collect(ops):
                    for op in ops:
                        if op['kind']=='slot':native_sizes.add(tuple(op['native_size']))
                        if op.get('ops'):collect(op['ops'])
                        if op.get('base_op'):collect([op['base_op']])
                for candidate_scene in renderer.data['scenes']:
                    if not candidate_scene.get('fixed'):collect(candidate_scene['ops'])
                if source.is_file() and native_sizes and (settings['fit']=='auto' or 'orientation' in settings):
                    artwork=engine.load_image(source,preserve_alpha=True)
                    try:
                        direction_source=engine.prepare_cover_artwork(artwork) if settings['fit']=='cover_bleed' else artwork
                        turned={0:direction_source}
                        for size in sorted(native_sizes):
                            direction=engine.orientation_info(direction_source,size,settings.get('orientation','original'),rotation=settings.get('rotation',0))
                            angle=direction['rotation']
                            if angle in (0,90,180,270) and angle not in turned:turned[angle]=engine.rotate_artwork(direction_source,angle)
                            info=engine.auto_fit_info(turned.get(angle,direction_source),size,policy=settings.get('fit_policy',1)) if settings['fit']=='auto' else dict(mode=settings['fit'],reason={'contain':'等比完整放入','cover':'等比铺满、居中裁切','cover_bleed':'去白边后等比铺满','stretch':'拉伸填满'}[settings['fit']])
                            fit_info.append(dict(size=list(size),mode=info['mode'],rotation=angle,reason=direction['reason']+'；'+info['reason']))
                    finally:artwork.close()
                destination = self.root / 'previews' / uuid.uuid4().hex
                destination.mkdir()
                previews = []
                indices=list(range(len(renderer.data['scenes']))) if scene is None else [scene]
                for position, (name, image) in enumerate(renderer.render(source, settings,scene_indices=indices)):
                    i=indices[position]
                    if self.control.stop.is_set():
                        image.close()
                        break
                    # Preview is rendered with export settings so framing matches final output.
                    image.save(destination / f'{i}.png')
                    previews.append({'name': name, 'url': f'/media/previews/{destination.name}/{i}.png','scene_index':i})
                    image.close()
                    self.update(progress=position + 1, total=len(indices), message='生成场景 ' + name)
                self.update(preview=previews, preview_signature=preview_stamp, preview_manifest=hashlib.sha256(manifest.read_bytes()).hexdigest(), preview_fit=fit_info, preview_template_id=template['id'], preview_settings=settings,
                            preview_source=requested_source, preview_scenes=[
                                {k: s.get(k,{}) for k in ('id', 'mode', 'targets', 'artwork_roles')} for s in template['scenes']])
                del renderer
            # Keep recent previews only; paths checked before any recursive removal.
            import shutil
            folders = sorted((p for p in (self.root / 'previews').iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
            for folder in folders[8:]:
                if folder.resolve().parent == (self.root / 'previews').resolve() and len(folder.name) == 32 and not folder.is_symlink():
                    shutil.rmtree(folder)
        return self.start('preview', work)

    def overview(self, ident, sources, settings, scene=None):
        template=copy.deepcopy(self.find(ident))
        settings=engine.settings_checked(settings)
        if not isinstance(sources,list) or not 1<=len(sources)<=100:raise ValueError('请选择 1–100 张印花')
        paths=list(dict.fromkeys(str(Path(p).resolve()) for p in sources))
        if any(not Path(p).is_file() or Path(p).suffix.lower() not in engine.EXTENSIONS for p in paths):raise ValueError('所选印花已不存在或格式不支持')
        if scene is None and len(paths)!=1:raise ValueError('整套总览一次选择一张印花')
        scenes=[s for s in template['scenes'] if s.get('mode')!='skip']
        if scene is not None and (isinstance(scene,bool) or not isinstance(scene,int) or scene<0 or scene>=len(scenes)):raise ValueError('请选择有效的轮播场景')
        if scene is None and len(scenes)>100:raise ValueError('整套场景超过 100 张，请减少导出场景')
        def work():
            from overview import compose_sheet
            from artwork_sources import signature
            folder=self.root/'previews'/'overview-cache';folder.mkdir(exist_ok=True)
            manifest=self.manifest(template)
            manifest_digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
            items=[]
            with self.cache.use(manifest):
                renderer=engine.Renderer(manifest)
                indices=list(range(len(renderer.data['scenes']))) if scene is None else [scene]
                total=len(paths)*len(indices)
                self.update(total=total,progress=0)
                pixel_settings={k:v for k,v in settings.items() if k not in ('naming','input_layout','input_exclude')}
                expected_scenes=[{k:s.get(k,{}) for k in ('id','mode','targets','artwork_roles')} for s in template['scenes']]
                for source in paths:
                    stamp=signature(source);targets={}
                    with self.lock:previous=copy.deepcopy(self.task)
                    reusable=(previous.get('preview_signature')==list(stamp) and previous.get('preview_manifest')==manifest_digest and previous.get('preview_template_id')==ident and {k:v for k,v in previous.get('preview_settings',{}).items() if k not in ('naming','input_layout','input_exclude')}==pixel_settings and previous.get('preview_source')==source and previous.get('preview_scenes')==expected_scenes)
                    for index in indices:
                        if self.control.stop.is_set():return
                        key=hashlib.sha256(json.dumps([manifest_digest,str(manifest.parent),source,stamp,pixel_settings,index],sort_keys=True).encode()).hexdigest()
                        target=folder/(key+'.png');targets[index]=target
                        previews=previous.get('preview',[])
                        preview=next((p for position,p in enumerate(previews) if p.get('scene_index',position)==index),None)
                        if not target.exists() and reusable and preview:
                            candidate=(self.root/preview['url'].removeprefix('/media/')).resolve()
                            if candidate.is_relative_to((self.root/'previews').resolve()) and candidate.is_file():
                                from PIL import Image
                                with Image.open(candidate) as image:image.thumbnail((900,900));image.save(target)
                    missing=[index for index in indices if not targets[index].exists()]
                    if missing:
                        rendered=renderer.render(source,settings,scene_indices=missing)
                        try:
                            for index,(_,image) in zip(missing,rendered):
                                try:
                                    if self.control.stop.is_set():return
                                    image.thumbnail((900,900));image.save(targets[index])
                                finally:image.close()
                                self.update(message='正在生成总览场景 '+renderer.data['scenes'][index]['name'])
                        finally:rendered.close()
                    if signature(source)!=stamp:
                        for target in targets.values():target.unlink(missing_ok=True)
                        raise ValueError('印花在生成过程中发生变化，请重新生成')
                    for index in indices:
                        target=targets[index];target.touch()
                        name=('场景 '+renderer.data['scenes'][index]['name']) if scene is None else Path(source).parent.name+'/'+Path(source).name
                        items.append((target,name));self.update(progress=len(items),message=f'正在生成总览 {len(items)}/{total}')
                title=template['name']+(' · 整套总览' if scene is None else ' · 场景 '+renderer.data['scenes'][scene]['name']+' · 多印花总览')
                result=folder/(uuid.uuid4().hex+'.jpg');size=compose_sheet(items,result,title)
                self.update(overview=dict(url='/media/previews/overview-cache/'+result.name,title=title,count=len(items),width=size[0],height=size[1],template_id=ident))
            keep={p for p,_ in items}|{result}
            for old in sorted(folder.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)[256:]:
                if old.is_file() and old not in keep and not old.is_symlink():old.unlink()
        return self.start('overview',work)

    def save_overview(self,folder):
        with self.lock:
            result=copy.deepcopy(self.task.get('overview'))
        if not result:raise ValueError('请先生成总览图')
        source=(self.root/result['url'].removeprefix('/media/')).resolve()
        if not source.is_relative_to((self.root/'previews').resolve()) or not source.is_file():raise ValueError('总览图已过期，请重新生成')
        destination=Path(folder)
        if not destination.is_dir():raise ValueError('请选择存在的保存文件夹')
        target=destination/('套图总览_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'.jpg')
        with target.open('xb') as stream:stream.write(source.read_bytes())
        return {'path':str(target)}

    def batch(self, ids, input_folder, output_folder, settings, limit=0, resume=None):
        templates = [copy.deepcopy(self.find(i)) for i in dict.fromkeys(ids)]
        if not templates:
            raise ValueError('请选择模板')
        settings = engine.settings_checked(settings)
        if not resume:
            settings.setdefault('input_layout','folders')
            settings.setdefault('input_exclude',['预览','参考','preview','reference'])
        limit = int(limit)
        if resume and 'input_layout' not in settings:
            from artwork_sources import legacy_discover
            images=legacy_discover(input_folder)
        else:images = self.input_images(input_folder,settings.get('input_exclude'))
        if not images:
            raise ValueError('印花文件夹里没有支持的图片')
        if limit < 0:
            raise ValueError('处理数量不能为负数')
        output = self.output_directory(output_folder,input_folder)
        for template in templates:
            catalog.updated_scenes(template, template['scenes'])
        with self.lock:
            self.idle_required()
            output.mkdir(parents=True,exist_ok=True)
        def work():
            with self.lock:
                self.preferences = dict(input=str(input_folder), output=str(output), settings=settings, limit=limit, preferences_version=2)
                self.save('preferences.json', self.preferences)
            count = min(limit, len(images)) if limit else len(images)
            results = []
            for index, template in enumerate(templates):
                if self.control.stop.is_set():
                    break
                manifest = self.manifest(template)
                template_output = output / engine.safe_name(template['name'])
                template_output.mkdir(exist_ok=True)
                self.update(message='正在套图：' + template['name'])
                def progress(n, total, name, status, dest):
                    if n == 0 and not name:
                        entry = {'path': str(dest), 'name': template['name'], 'template_ids': [template['id']],
                                 'template_names': [template['name']], 'status': '运行中',
                                 'created_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'settings': settings,
                                 'input': str(input_folder), 'output': str(output),
                                 'counts': {'success': 0, 'failed': 0}}
                        with self.lock:
                            self.history = [entry] + [h for h in self.history if h['path'] != str(dest)]
                            self.save('history.json', self.history)
                        self.update(results=results + [str(dest)])
                    self.update(progress=index * count + n, total=len(templates) * count,
                                message=f'{template["name"]} · {name} · {status}')
                with self.cache.use(manifest):
                    dest, state = engine.batch(manifest, input_folder, template_output, settings,
                        limit=limit, resume=resume, control=self.control, progress=progress)
                records = state['records']
                entry = {'path': str(dest), 'name': template['name'], 'template_ids': [template['id']],
                         'template_names': [template['name']], 'status': state['status'],
                         'created_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'settings': settings,
                         'input': str(input_folder), 'output': str(output), 'counts': {
                             'success': sum(r['status'] == '成功' for r in records.values()),
                             'failed': sum(r['status'] == '失败' for r in records.values())}}
                with self.lock:
                    self.history = [entry] + [h for h in self.history if h['path'] != str(dest)]
                    self.save('history.json', self.history)
                results.append(str(dest))
                self.update(results=list(results))
                gc.collect()
        return self.start('batch', work)

    def resume(self, path):
        entry = next((h for h in self.history if h['path'] == path), None)
        if not entry:
            raise ValueError('这个批次不在本机记录中')
        state = json.loads((Path(path) / '运行状态.json').read_text(encoding='utf-8'))
        identity = state['identity']
        return self.batch(entry['template_ids'], identity['input'], entry['output'],
                          identity['settings'], len(identity['files']), resume=path)

    def command(self, action):
        with self.lock:
            if not self.control or self.task['status'] not in ('running', 'paused', 'stopping'):
                raise ValueError('当前没有运行中的任务')
            if action == 'stop':
                self.control.stop.set()
                self.update(status='stopping', message='正在停止，等待当前产品完成后保存进度…')
            elif self.task['kind'] != 'batch':
                raise ValueError('只有批量套图可以暂停')
            elif action == 'pause':
                self.control.paused.set()
                self.update(status='paused', message='已请求暂停，正在处理的产品完成后暂停。')
            elif action == 'resume':
                self.control.paused.clear()
                self.update(status='running', message='继续处理…')
            else:
                raise ValueError('任务操作无效')
        return {'ok': True}

    def cache_command(self, action, limit_bytes=None):
        with self.lock:
            self.idle_required()
            if action == 'limit':
                limit_bytes = int(limit_bytes)
                if not 64 * 1024**2 <= limit_bytes <= 1024**4:
                    raise ValueError('缓存容量应在 64 MB 到 1 TB 之间')
                self.cache.limit_bytes = limit_bytes
                self.save('cache-settings.json', {'limit_bytes': limit_bytes})
                self.cache.clean(all_unused=False)
            elif action == 'clean':
                self.cache.clean()
            else:
                raise ValueError('缓存操作无效')
        return {'ok': True}

    def remove(self, ident):
        with self.lock:
            self.idle_required()
            self.templates = [t for t in self.templates if t['id'] != ident]
            self.save('templates.json', self.templates)
        return {'ok': True}
