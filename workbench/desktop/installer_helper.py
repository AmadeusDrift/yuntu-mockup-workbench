"""Offline, recoverable file updates. Runs from the installer's temporary payload."""
import argparse
from contextlib import contextmanager
import configparser
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time
import uuid

PRODUCT='yuntu-mockup-workbench'
MANIFEST='resources/app/install-manifest.json'
MARKER='.workbench-update.json'
CONFIGS=('templates.json','preferences.json','history.json','cache-settings.json','desktop-seeded.json')


class UpdateError(Exception):
    def __init__(self,message,code=10):
        super().__init__(message);self.code=code


def native_path(path):
    """Use extended Windows paths without exposing those prefixes in records/UI."""
    value=os.path.abspath(path)
    if os.name=='nt' and not value.startswith('\\\\?\\'):
        value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
    return Path(value)


def read_json(path):
    return json.loads(native_path(path).read_text(encoding='utf-8-sig'))


def write_json(path,value):
    path=native_path(path);temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):raise UpdateError('程序版本信息无效，安装已停止。')
    return tuple(map(int,value.split('.')))


def safe_path(root,relative):
    if not isinstance(relative,str) or '\\' in relative or ':' in relative:raise UpdateError('安装文件路径无效。')
    parts=PurePosixPath(relative).parts
    if not parts or relative!='/'.join(parts) or PurePosixPath(relative).is_absolute() or any(p in ('.','..') or p.endswith((' ','.')) for p in parts):
        raise UpdateError('安装文件路径超出程序目录。')
    result=root.joinpath(*parts)
    for item in (result,*result.parents):
        item_io=native_path(item)
        if item_io.is_symlink() or (hasattr(item_io,'is_junction') and item_io.is_junction()):raise UpdateError('安装位置包含链接，请选择普通文件夹。')
        if item==root:break
    if native_path(root).resolve() not in native_path(result).resolve().parents:raise UpdateError('安装文件路径超出程序目录。')
    return result


def replace_file(source,target):
    source=native_path(source);target=native_path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name('.yuntu-update-'+target.name+'.tmp')
    try:
        shutil.copyfile(source,temp)
        with temp.open('r+b') as stream:os.fsync(stream.fileno())
        for attempt in range(26):
            try:
                os.replace(temp,target);break
            except PermissionError:
                if attempt==25:raise
                time.sleep(.2)
    finally:
        if temp.exists():temp.unlink()


@contextmanager
def update_lock(target):
    import msvcrt
    target.parent.mkdir(parents=True,exist_ok=True)
    name='.yuntu-update-lock-'+hashlib.sha256(str(target).casefold().encode()).hexdigest()[:16]+'.lock'
    with (target.parent/name).open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise UpdateError('另一个安装程序正在更新，请等待它完成。')
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def process_paths():
    if os.name!='nt':return {}
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD);kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=(wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    ids=(wintypes.DWORD*65536)();needed=wintypes.DWORD()
    if not psapi.EnumProcesses(ids,ctypes.sizeof(ids),ctypes.byref(needed)):raise UpdateError('无法检查正在运行的软件，请关闭软件后重试。')
    result={}
    for pid in ids[:needed.value//ctypes.sizeof(wintypes.DWORD)]:
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:continue
        try:
            buffer=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(length)):result[pid]=buffer.value
        finally:kernel.CloseHandle(handle)
    return result


def running_processes(target,data_root):
    candidates={str(target/'套图工作台.exe').casefold(),str(target/'resources/app/python/runtime/python.exe').casefold()}
    paths=process_paths()
    # Also cover a portable copy currently using this same profile. Validate the
    # actual PID's executable and product, rather than trusting a stale PID alone.
    try:
        state=read_json(data_root/'desktop-status.json');pid=state['desktop_pid']
        executable=Path(paths[pid]);info=read_json(executable.parent/'resources/app/package.json')
        if info.get('name')==PRODUCT:
            candidates.add(str(executable).casefold())
            candidates.add(str(executable.parent/'resources/app/python/runtime/python.exe').casefold())
    except (OSError,ValueError,KeyError,TypeError):pass
    return {pid:path for pid,path in paths.items() if path.casefold() in candidates}


def close_applications(target,data_root,timeout=300):
    from ctypes import wintypes
    user=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.EnumWindows.argtypes=(callback_type,wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.DWORD))
    user.IsWindowVisible.argtypes=(wintypes.HWND,)
    user.GetClassNameW.argtypes=(wintypes.HWND,wintypes.LPWSTR,ctypes.c_int)
    user.PostMessageW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
    deadline=time.monotonic()+timeout;sent=set()
    while True:
        running=running_processes(target,data_root)
        if not running:return
        @callback_type
        def close_window(hwnd,_):
            pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            name=ctypes.create_unicode_buffer(256);user.GetClassNameW(hwnd,name,256)
            if pid.value in running and user.IsWindowVisible(hwnd) and name.value.startswith('Chrome_WidgetWin') and hwnd not in sent:
                user.PostMessageW(hwnd,0x0010,0,0);sent.add(hwnd)
            return True
        user.EnumWindows(close_window,0)
        if time.monotonic()>=deadline:raise UpdateError('旧版仍在运行，尚未覆盖任何文件。请等待进度保存并关闭软件后重试。',20)
        time.sleep(.2)


class Updater:
    def __init__(self,payload,target,data_root):
        self.payload=Path(os.path.abspath(payload));self.target=Path(os.path.abspath(target));self.data=Path(os.path.abspath(data_root))
        self.marker=self.target/MARKER

    def inventory(self,root):
        raw=read_json(root/MANIFEST)
        if raw.get('product')!=PRODUCT:raise UpdateError('安装文件不属于套图工作台。')
        version(raw.get('version'));entries={};seen=set()
        for entry in raw['files']:
            relative=entry['path'];safe_path(root,relative)
            if relative.casefold() in seen:raise UpdateError('安装清单存在重复文件。')
            entries[relative]=entry;seen.add(relative.casefold())
        if '套图工作台.exe' not in entries or 'resources/app/package.json' not in entries:raise UpdateError('安装清单不完整。')
        return raw,entries

    def check(self,check_running=True):
        info,entries=self.inventory(self.payload)
        package=read_json(self.payload/'resources/app/package.json')
        if package.get('name')!=PRODUCT or package.get('version')!=info['version']:raise UpdateError('安装包版本不一致。')
        if self.target==self.payload or self.payload in self.target.parents or self.target in self.payload.parents:raise UpdateError('安装目录与临时文件目录不能互相包含。')
        safe_path(self.target,'套图工作台.exe')
        if self.marker.exists():
            if check_running and running_processes(self.target,self.data):raise UpdateError('旧版正在运行，需要先保存进度并关闭软件。',20)
            return dict(version=info['version'],previous_version='pending-recovery'),entries
        old=None;package_path=self.target/'resources/app/package.json'
        if native_path(package_path).exists():
            old=read_json(package_path)
            if old.get('name')!=PRODUCT:raise UpdateError('这个文件夹属于其他软件，请选择套图工作台的安装位置。')
            if version(old.get('version'))>version(info['version']):raise UpdateError('电脑上已有更新版本，已阻止安装旧版本。',21)
        elif self.target.exists() and any(p.name!=MARKER for p in self.target.iterdir()):
            raise UpdateError('所选文件夹已有其他文件，请选择空文件夹或现有套图工作台目录。')
        if check_running and running_processes(self.target,self.data):raise UpdateError('旧版正在运行，需要先保存进度并关闭软件。',20)
        return dict(version=info['version'],previous_version=old['version'] if old else None),entries

    def validate_payload(self,entries):
        for name,entry in entries.items():
            source=native_path(safe_path(self.payload,name))
            if not source.is_file() or source.stat().st_size!=entry['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest()!=entry['sha256']:
                raise UpdateError('安装文件校验失败，尚未覆盖旧版，请重新获取完整安装包。')

    def extract(self):
        if self.target.exists() and any(self.target.iterdir()):raise UpdateError('仅解包模式需要一个空文件夹。')
        result,entries=self.check(check_running=False);self.validate_payload(entries)
        for name in sorted(entries):replace_file(safe_path(self.payload,name),safe_path(self.target,name))
        replace_file(self.payload/MANIFEST,self.target/MANIFEST)
        return result

    def recover(self):
        if not self.marker.exists():return False
        marker=read_json(self.marker);name=marker.get('transaction','')
        if not re.fullmatch(r'\.yuntu-update-[0-9a-f]{32}',name):raise UpdateError('更新恢复记录无效，请保留程序目录并联系维护人员。',11)
        transaction=self.target.parent/name
        if transaction.is_symlink() or (hasattr(transaction,'is_junction') and transaction.is_junction()):raise UpdateError('更新恢复目录无效。',11)
        journal=read_json(transaction/'journal.json')
        if journal.get('product')!=PRODUCT or Path(journal.get('target','')).resolve()!=self.target.resolve():raise UpdateError('更新恢复记录与程序目录不一致。',11)
        # Validate the complete record before any restore/removal.
        for name in journal['files']:safe_path(self.target,name)
        for name in journal['existed']:safe_path(transaction/'previous',name)
        directories=[safe_path(self.target,name) for name in journal.get('created_dirs',[])]
        for name in journal['files']:
            target=safe_path(self.target,name)
            if name in journal['existed']:replace_file(safe_path(transaction/'previous',name),target)
            elif native_path(target).exists():native_path(target).unlink()
            temporary=native_path(target.with_name('.yuntu-update-'+target.name+'.tmp'))
            if temporary.exists():temporary.unlink()
        self.marker.unlink()
        for directory in sorted(directories,key=lambda p:len(p.parts),reverse=True):
            try:native_path(directory).rmdir()
            except OSError:pass  # Never remove a directory with user-added contents.
        if journal.get('target_created'):
            try:self.target.rmdir()
            except OSError:pass
        shutil.rmtree(native_path(transaction))
        return True

    def install(self,close_running=False):
        with update_lock(self.target):
            if not self.marker.exists():self.check(check_running=False)
            if running_processes(self.target,self.data):
                if not close_running:raise UpdateError('旧版正在运行，需要先保存进度并关闭软件。',20)
                close_applications(self.target,self.data)
            self.recover()
            result,entries=self.check()
            self.validate_payload(entries)
            previous={}
            if (self.target/MANIFEST).exists():_,previous=self.inventory(self.target)
            changed={}
            for name,entry in entries.items():
                current_file=native_path(safe_path(self.target,name))
                if (not current_file.is_file() or current_file.stat().st_size!=entry['bytes'] or
                    hashlib.sha256(current_file.read_bytes()).hexdigest()!=entry['sha256']):changed[name]=entry
            current={name.casefold() for name in entries}
            obsolete={name:entry for name,entry in previous.items() if name.casefold() not in current}
            identity={name.casefold():name for name in changed}
            identity.update({name.casefold():name for name in previous if name.casefold() in identity or name in obsolete})
            identity[MANIFEST.casefold()]=MANIFEST
            names=sorted(identity.values())
            existed=[name for name in names if native_path(safe_path(self.target,name)).is_file()]
            created_dirs=set();target_created=not self.target.exists()
            for name in names:
                directory=safe_path(self.target,name).parent
                while directory!=self.target:
                    if not directory.exists():created_dirs.add(directory.relative_to(self.target).as_posix())
                    directory=directory.parent
            need=sum(native_path(self.payload/name).stat().st_size for name in changed)+sum(native_path(self.target/name).stat().st_size for name in existed)+32*1024**2
            if shutil.disk_usage(self.target.parent).free<need:raise UpdateError('安装盘剩余空间不足，请腾出空间后重试。')
            backup=self.data/'update-backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
            backup.mkdir(parents=True)
            for name in CONFIGS:
                source=self.data/name
                if source.is_file():shutil.copyfile(source,backup/name)
            write_json(backup/'backup-info.json',dict(result,target=str(self.target),created=time.time()))
            transaction=self.target.parent/('.yuntu-update-'+uuid.uuid4().hex)
            transaction.mkdir()
            print('Backing up the previous program...',flush=True)
            try:
                for name in existed:
                    target=safe_path(transaction/'previous',name)
                    replace_file(safe_path(self.target,name),target)
                write_json(transaction/'journal.json',dict(product=PRODUCT,target=str(self.target),files=names,existed=existed,
                                                          created_dirs=sorted(created_dirs),target_created=target_created))
                self.target.mkdir(parents=True,exist_ok=True)
                write_json(self.marker,dict(transaction=transaction.name))
            except Exception:
                shutil.rmtree(native_path(transaction));raise
            # Backing up hundreds of MB takes time; the user may have started
            # the old application since preflight. Do not begin an unsafe copy.
            try:
                if running_processes(self.target,self.data):
                    if not close_running:raise UpdateError('旧版正在运行，尚未覆盖文件。请关闭软件后重试。',20)
                    close_applications(self.target,self.data)
            except Exception:
                self.marker.unlink();shutil.rmtree(native_path(transaction));raise
            try:
                print('Installing verified program files...',flush=True)
                # Publish the startup guard first, before other application code.
                order=sorted(changed,key=lambda name:(0 if name=='resources/app/main.cjs' else 1 if name=='套图工作台.exe' else 2,name))
                for name in order:replace_file(safe_path(self.payload,name),safe_path(self.target,name))
                replace_file(self.payload/MANIFEST,self.target/MANIFEST)
                for name in obsolete:
                    old=native_path(safe_path(self.target,name))
                    if old.exists():old.unlink()
                self.marker.unlink()
            except Exception as error:
                try:self.recover()
                except Exception as recovery:
                    raise UpdateError('更新未完成。恢复文件仍已保留，请关闭软件后重新运行安装包恢复旧版。'+str(recovery),11) from error
                raise UpdateError('更新失败，已恢复原来的程序。设置和批次记录保留。'+str(error)) from error
            shutil.rmtree(native_path(transaction))
            result['settings_backup']=str(backup)
            write_json(self.data/'last-update.json',dict(result,updated=time.time()))
            return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--payload',type=Path,required=True);parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--action',choices=('check','install','extract'),default='install');parser.add_argument('--close-running',action='store_true')
    args=parser.parse_args();result={};code=0
    try:
        updater=Updater(args.payload,args.target,args.data_root)
        result=updater.check()[0] if args.action=='check' else updater.extract() if args.action=='extract' else updater.install(close_running=args.close_running)
        message='安装完成，已有模板、设置和批次记录保留。'
    except Exception as error:
        code=getattr(error,'code',10);message=str(error)
    ini=configparser.ConfigParser(interpolation=None);ini['update']={**{key:str(value) for key,value in result.items()},'message':message,'code':str(code)}
    with args.result.open('w',encoding='utf-16') as stream:ini.write(stream)
    print(message.encode('ascii','backslashreplace').decode(),flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
