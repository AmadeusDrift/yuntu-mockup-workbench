"""Deterministic output names planned before concurrent rendering begins."""
import re
from pathlib import Path
from string import Formatter


def checked_naming(value, safe_name):
    if not isinstance(value, dict):
        raise ValueError('导出命名规则无效')
    start = value.get('start', 1)
    if isinstance(start, bool) or not isinstance(start, int) or not 1 <= start <= 999999:
        raise ValueError('起始编号请填写 1–999999 的整数')
    result = dict(folder=value.get('folder', '{name}'), image=value.get('image', '{scene:02}'), start=start)
    for key, allowed in [('folder', {'name', 'index'}), ('image', {'name', 'index', 'scene', 'folder'})]:
        pattern = result[key]
        if not isinstance(pattern, str) or not pattern or len(pattern) > 150:
            raise ValueError('命名规则不能为空或过长')
        try:
            for literal, field, spec, conversion in Formatter().parse(pattern):
                if field is None: continue
                if field not in allowed or conversion or (spec and (field in ('name', 'folder') or not re.fullmatch(r'0[1-6]d?', spec))):
                    raise ValueError('命名变量或补零位数无效')
            sample = pattern.format(name='印花', index=start, scene=1, folder='001')
            if safe_name(sample) != sample:
                raise ValueError('命名规则包含无效文件名字符或超出长度限制')
        except (ValueError, KeyError, IndexError) as exc:
            raise ValueError('导出命名规则无效：' + str(exc)) from exc
    return result


def plan_outputs(files, scenes, settings, safe_name, source_root=None):
    rules = checked_naming(settings['naming'], safe_name) if 'naming' in settings else dict(folder='{name}', image='{name}_{scene:02}', start=1)
    from artwork_sources import group_members, ROLES
    plans, folders, products, members_cache = {}, set(), {}, {}
    for offset, (path, _, _) in enumerate(files):
        role=None
        if 'input_layout' not in settings:
            if path.parent not in members_cache:
                try:members_cache[path.parent]=group_members(path.parent,allow_single=True)
                except ValueError:members_cache[path.parent]=None
            role=next((r for r,p in (members_cache[path.parent] or {}).items() if p==path),None)
        grouped=settings.get('input_layout')=='folders' and source_root is not None and path.parent!=Path(source_root)
        product_key=path.parent if grouped or role else path
        if product_key not in products:products[product_key]=len(products)
        values = dict(name=safe_name(('__'.join(path.parent.relative_to(source_root).parts) if grouped else path.parent.name) if grouped or role else path.stem), index=rules['start'] + products[product_key])
        folder = rules['folder'].format(**values)
        names = [rules['image'].format(**values, folder=folder, scene=i) + '.' + settings['format'] for i in range(1, scenes + 1)]
        if safe_name(folder) != folder or ('naming' in settings and any(safe_name(name) != name for name in names)):
            raise ValueError('导出名称过长或包含无效字符，请缩短命名规则')
        folder = folder + '/' + safe_name(path.stem) if grouped else folder + '/' + ROLES[role] if role else folder
        if folder.casefold() in folders or any(folder.casefold().startswith(f+'/') or f.startswith(folder.casefold()+'/') for f in folders):
            raise ValueError('发现重名产品文件夹，请在规则中加入 {index:03} 或调整原图名称')
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError('导出图片重名，请在规则中加入 {scene:02}')
        folders.add(folder.casefold())
        from artwork_sources import source_key
        plans[source_key(path,source_root) if source_root else path.name] = dict(folder=folder, files=names)
    return plans
