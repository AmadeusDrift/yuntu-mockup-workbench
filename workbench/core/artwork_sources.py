"""Shared discovery for preview and export; a triptych folder is one product."""
import hashlib
import json
import re
from pathlib import Path
from PIL import Image, ImageOps

ROLES={'left':'左','center':'中','right':'右'}
EXTENSIONS={'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}

def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())

def group_members(folder, allow_single=False):
    folder=Path(folder)
    if not folder.is_dir():return None
    found={}
    for path in folder.iterdir():
        if not linked(path) and path.is_file() and path.suffix.lower() in EXTENSIONS:
            for role,name in ROLES.items():
                if path.stem.casefold() in (role,name):
                    if role in found:raise ValueError(f'{folder.name}：{name}图有多个文件，请只保留一张')
                    found[role]=path
    if not found:return None
    if allow_single and len(found)==1:return None
    missing=[name for role,name in ROLES.items() if role not in found]
    if missing:raise ValueError(f'{folder.name}：缺少'+ '、'.join(missing)+'图，请补齐左、中、右')
    return found

def natural(value):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)',value)]

def discover(folder,extensions=EXTENSIONS,exclude=None):
    root=Path(folder)
    if not str(folder).strip() or not root.is_dir():raise ValueError('请选择存在的印花文件夹')
    excluded=['预览','参考','preview','reference'] if exclude is None else exclude
    result=[]
    def order(path):
        roles=['左','中','右','left','center','right']
        return (0,roles.index(path.stem.casefold())%3) if path.stem.casefold() in roles else (1,natural(path.name))
    def walk(directory):
        if (directory/'运行状态.json').is_file():return
        for path in sorted(directory.iterdir(),key=order):
            if linked(path):continue
            if path.is_dir():walk(path)
            elif path.suffix.lower() in extensions and not any(word.casefold() in path.name.casefold() for word in excluded if word):result.append(path)
    walk(root)
    return result


def legacy_discover(folder,extensions=EXTENSIONS):
    """Keep pre-0.3.5 batch identities stable when resuming saved settings."""
    root=Path(folder);result=[];single=[];groups=[]
    def walk(directory):
        if (directory/'运行状态.json').is_file():return
        members=group_members(directory,allow_single=True)
        if members:
            groups.append(directory);result.extend(members[r] for r in ROLES);return
        children=sorted(directory.iterdir(),key=lambda p:natural(p.name))
        if any(p.is_file() and p.suffix.lower() in EXTENSIONS and p.stem.casefold() in (*ROLES,*ROLES.values()) for p in children):single.append(directory)
        for path in children:
            if linked(path):continue
            if path.is_dir():walk(path)
            elif path.suffix.lower() in extensions:result.append(path)
    walk(root)
    if groups:
        for directory in single:group_members(directory)
    return result

def source_key(path,root):
    relative=Path(path).relative_to(root).as_posix()
    return Path(path).name if relative=='.' else relative

def signature(path):
    path=Path(path);members=group_members(path)
    if members:
        rows=[(role,p.name,p.stat().st_size,p.stat().st_mtime_ns) for role,p in sorted(members.items())]
        return sum(r[2] for r in rows),int.from_bytes(hashlib.sha256(json.dumps(rows).encode()).digest()[:8],'big')
    stat=path.stat();return stat.st_size,stat.st_mtime_ns

def thumbnail(path):
    members=group_members(path)
    if not members:
        with Image.open(path) as raw:return ImageOps.exif_transpose(raw).convert('RGBA')
    result=Image.new('RGBA',(330,150),'#f3f8fe')
    for i,role in enumerate(ROLES):
        with Image.open(members[role]) as raw:
            im=ImageOps.exif_transpose(raw).convert('RGBA');im.thumbnail((106,150))
            result.paste(im,(i*110+(106-im.width)//2,(150-im.height)//2),im);im.close()
    return result

def slots(ops):
    for op in ops:
        if op['kind']=='slot':yield op
        yield from slots(op.get('ops',[]))
        if op.get('base_op'):yield from slots([op['base_op']])

def validate_roles(data):
    active=[op for scene in data['scenes'] if not scene.get('fixed') for op in slots(scene['ops'])]
    if not active:return
    if any(op.get('artwork_role') not in ROLES for op in active):
        raise ValueError('三联画需要先在模板的印花图层设置左、中、右对应关系，再保存场景设置')
    missing=set(ROLES)-{op['artwork_role'] for op in active}
    if missing:raise ValueError('模板缺少对应印区：'+'、'.join(ROLES[r] for r in ROLES if r in missing))
