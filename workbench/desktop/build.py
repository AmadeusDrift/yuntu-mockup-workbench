"""Offline Windows desktop distribution using the already cached official binaries."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from PIL import Image, ImageDraw

VERSION = json.loads((Path(__file__).parent / 'package.json').read_text(encoding='utf-8'))['version']
project = bootstrap.ROOT.parent
release = project / 'release'
stage = release / ('套图工作台-v' + VERSION)
app_root = stage / 'resources' / 'app'


def icon(destination):
    image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 254, 254), radius=55, fill='#2163ff')
    for x, y in ((53, 48), (136, 48), (53, 136), (136, 136)):
        draw.rounded_rectangle((x, y, x+68, y+73), radius=8, fill='white')
        draw.rectangle((x+8, y+9, x+60, y+49), fill='#b6d3ff')
        draw.polygon([(x+8,y+49),(x+29,y+25),(x+41,y+39),(x+50,y+30),(x+60,y+49)],fill='#4385ff')
    image.save(destination, sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


def build_portable():
    release.mkdir(exist_ok=True)
    if stage.resolve().parent != release.resolve():
        raise ValueError('发行目录范围无效')
    if not stage.exists():
        archive = next((Path.home()/'AppData/Local/electron/Cache').glob('*/electron-v45.1.0-win32-x64.zip'))
        print('Extracting cached Electron 45.1.0...', flush=True)
        stage.mkdir()
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                target = (stage / entry.filename).resolve()
                if stage.resolve() not in target.parents:
                    raise ValueError('运行环境压缩包路径无效')
            package.extractall(stage)
    app_root.mkdir(parents=True, exist_ok=True)
    for name in ('main.cjs','preload.cjs','package.json','installer_helper.py','updater.cjs','update-public-key.pem'):
        shutil.copyfile(Path(__file__).parent/name,app_root/name)
    print('Copying bundled Python and PSD engine...', flush=True)
    for name in ('runtime', 'vendor'):
        target=app_root/'python'/name
        if not target.exists():
            shutil.copytree(bootstrap.PORTABLE/name,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    server=app_root/'workbench';server.mkdir(exist_ok=True)
    for source in bootstrap.ROOT.glob('*.py'):
        if source.name not in ('acceptance.py',):
            shutil.copyfile(source,server/source.name)
    for name in ('core','web'):
        shutil.copytree(bootstrap.ROOT/name,server/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    seed=app_root/'seed';seed.mkdir(exist_ok=True)
    # A transferable build must not reference PSDs on the build machine.
    (seed/'templates.json').write_text('[]\n',encoding='utf-8')
    shutil.copyfile(project/'使用套图工作台.md',app_root/'使用说明.md')
    shutil.copyfile(bootstrap.ROOT/'SOURCE.md',app_root/'SOURCE.md')
    icon(app_root/'app.ico')
    electron=stage/'electron.exe'
    executable=stage/'套图工作台.exe'
    if electron.exists():
        electron.rename(executable)
    wer=stage/'electron_wer.dll'
    if wer.exists():
        wer.rename(stage/'套图工作台_wer.dll')
    (stage/'请先看这里.txt').write_text('双击“套图工作台.exe”即可。无需安装Python、Photoshop或打开浏览器。\n整个文件夹需要一起保留。模板和批次设置保存在个人应用数据目录，原PSD和印花不会修改。\n',encoding='utf-8-sig')
    inventory=app_root/'install-manifest.json'
    inventory.write_text(json.dumps({'product':'yuntu-mockup-workbench','version':VERSION,'files':[
        {'path':p.relative_to(stage).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(stage.rglob('*')) if p.is_file() and p!=inventory]},ensure_ascii=False),encoding='utf-8')
    manifest={'version':VERSION,'electron':'45.1.0','files':sum(p.is_file() for p in stage.rglob('*')),
              'bytes':sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()),
              'source_sha256':{str(p.relative_to(bootstrap.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in bootstrap.ROOT.rglob('*') if p.is_file() and ('data' not in p.relative_to(bootstrap.ROOT).parts)
                  and p.suffix in ('.py','.js','.cjs','.css','.html')}}
    (release/'desktop-build.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'portable':str(executable),'bytes':manifest['bytes']}),flush=True)


def build_installer():
    makensis=Path(os.environ['WORKBENCH_MAKENSIS']) if os.environ.get('WORKBENCH_MAKENSIS') else next((Path.home()/'AppData/Local/electron-builder/Cache').glob('nsis-*/nsis-*/Bin/makensis.exe'))
    installer=release/'套图工作台安装.exe'
    source=release/'installer.nsi'
    def quote(value):return str(value).replace('$','$$').replace('"','$\\"')
    lines=[
        'Unicode true', f'Name "套图工作台 {VERSION}"', f'OutFile "{quote(installer)}"',
        'InstallDir "$LOCALAPPDATA\\Programs\\YuntuMockupWorkbench"','RequestExecutionLevel user',
        'InstallDirRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "UninstallString"',
        'SetCompressor zlib','!include "MUI2.nsh"','!include "FileFunc.nsh"',
        'AllowSkipFiles off','Var ExtractOnly','Var NoRegister','Var CloseArgument','Var ResultMessage','Var AutoRestart','Var DataProfile',
        'Function .onInit','${GetParameters} $0','ClearErrors',
        '${GetOptions} $0 "/EXTRACTONLY" $1','IfErrors normal_install',
        'StrCpy $ExtractOnly "1"','normal_install:','ClearErrors',
        '${GetOptions} $0 "/NOREGISTER" $1','IfErrors options_done','StrCpy $NoRegister "1"','options_done:','ClearErrors',
        '${GetOptions} $0 "/AUTORESTART" $1','IfErrors auto_done','StrCpy $AutoRestart "1"','SetSilent silent','auto_done:', 'StrCpy $DataProfile "$APPDATA\\套图工作台"', 'ClearErrors', '${GetOptions} $0 "/PROFILE=" $1', 'IfErrors profile_done', 'StrCpy $DataProfile $1', 'profile_done:', 'FunctionEnd',
        f'!define MUI_ICON "{quote(app_root/"app.ico")}"',
        '!define MUI_WELCOMEPAGE_TEXT "安装或更新套图工作台。已有模板、设置和批次记录会保留；更新前会备份设置。请勿在更新过程中启动软件。"',
        '!insertmacro MUI_PAGE_WELCOME','!insertmacro MUI_PAGE_DIRECTORY','!insertmacro MUI_PAGE_INSTFILES',
        '!define MUI_FINISHPAGE_RUN "$INSTDIR\\套图工作台.exe"','!insertmacro MUI_PAGE_FINISH',
        '!insertmacro MUI_UNPAGE_CONFIRM','!insertmacro MUI_UNPAGE_INSTFILES','!insertmacro MUI_LANGUAGE "SimpChinese"',
        'Section "安装"','SetShellVarContext current','InitPluginsDir','SetOutPath "$PLUGINSDIR\\payload"',f'File /r "{quote(stage)}\\*"',
        'StrCmp $ExtractOnly "1" extract_only',
        'DetailPrint "正在检查已有版本和运行状态…"',
        'nsExec::ExecToLog \'"$PLUGINSDIR\\payload\\resources\\app\\python\\runtime\\python.exe" -I -B "$PLUGINSDIR\\payload\\resources\\app\\installer_helper.py" --payload "$PLUGINSDIR\\payload" --target "$INSTDIR" --data-root "$DataProfile" --result "$PLUGINSDIR\\update-result.ini" --action check\'',
        'Pop $0','StrCmp $0 "0" apply_update','StrCmp $0 "20" close_running update_failed',
        'close_running:','IfSilent update_failed',
        'MessageBox MB_OKCANCEL|MB_ICONINFORMATION "检测到套图工作台正在运行。点击确定，将保存套图进度并关闭软件，然后继续更新。" IDOK allow_close',
        'SetErrorLevel 20','Abort','allow_close:','StrCpy $CloseArgument "--close-running"',
        'apply_update:','DetailPrint "正在备份设置并更新程序，请稍候…"','Delete "$PLUGINSDIR\\update-result.ini"',
        'nsExec::ExecToLog \'"$PLUGINSDIR\\payload\\resources\\app\\python\\runtime\\python.exe" -I -B "$PLUGINSDIR\\payload\\resources\\app\\installer_helper.py" --payload "$PLUGINSDIR\\payload" --target "$INSTDIR" --data-root "$DataProfile" --result "$PLUGINSDIR\\update-result.ini" --action install $CloseArgument\'',
        'Pop $0','StrCmp $0 "0" register_install update_failed',
        'extract_only:',
        'nsExec::ExecToLog \'"$PLUGINSDIR\\payload\\resources\\app\\python\\runtime\\python.exe" -I -B "$PLUGINSDIR\\payload\\resources\\app\\installer_helper.py" --payload "$PLUGINSDIR\\payload" --target "$INSTDIR" --data-root "$DataProfile" --result "$PLUGINSDIR\\update-result.ini" --action extract\'',
        'Pop $0','StrCmp $0 "0" install_done update_failed',
        'register_install:','StrCmp $NoRegister "1" install_done','SetOutPath "$INSTDIR"',
        'WriteUninstaller "$INSTDIR\\卸载套图工作台.exe"',
        'CreateDirectory "$SMPROGRAMS\\套图工作台"',
        'CreateShortcut "$DESKTOP\\套图工作台.lnk" "$INSTDIR\\套图工作台.exe" "" "$INSTDIR\\resources\\app\\app.ico"',
        'CreateShortcut "$SMPROGRAMS\\套图工作台\\套图工作台.lnk" "$INSTDIR\\套图工作台.exe" "" "$INSTDIR\\resources\\app\\app.ico"',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DisplayName" "套图工作台"',
        f'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DisplayVersion" "{VERSION}"',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "InstallLocation" "$INSTDIR"',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DataLocation" "$DataProfile"',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "UninstallString" \'"$INSTDIR\\卸载套图工作台.exe"\'',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DisplayIcon" "$INSTDIR\\resources\\app\\app.ico"',
        'Goto install_done','update_failed:','ReadINIStr $ResultMessage "$PLUGINSDIR\\update-result.ini" "update" "message"',
        'StrCmp $ResultMessage "" 0 error_message_ready','StrCpy $ResultMessage "安装未完成，旧版未被覆盖或恢复记录已保留。请关闭软件后重新运行安装包。"',
        'error_message_ready:','DetailPrint "$ResultMessage"','StrCmp $AutoRestart "1" show_update_error','IfSilent skip_update_error','show_update_error:','MessageBox MB_OK|MB_ICONSTOP "$ResultMessage"','skip_update_error:',
        'SetErrorLevel 10','Abort','install_done:','StrCmp $AutoRestart "1" 0 restart_done','StrCmp $ExtractOnly "1" restart_done',
        'Exec \'"$INSTDIR\\套图工作台.exe" --data-dir="$DataProfile"\'','restart_done:','SectionEnd','Section "Uninstall"','SetShellVarContext current']
    # Delete only installed files, never recursively remove a user-selected directory.
    for entry in stage.rglob('*'):
        if entry.is_file():lines.append('Delete "$INSTDIR\\'+quote(entry.relative_to(stage))+'"')
    lines.extend(['Delete "$INSTDIR\\卸载套图工作台.exe"',
        'Delete "$DESKTOP\\套图工作台.lnk"','Delete "$SMPROGRAMS\\套图工作台\\套图工作台.lnk"',
        'RMDir "$SMPROGRAMS\\套图工作台"',
        'DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench"'])
    for folder in sorted((p for p in stage.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        lines.append('RMDir "$INSTDIR\\'+quote(folder.relative_to(stage))+'"')
    lines.extend(['RMDir "$INSTDIR"','SectionEnd'])
    source.write_text('\n'.join(lines),encoding='utf-8-sig')
    print('Building offline installer...',flush=True)
    subprocess.run([str(makensis),'/V2',str(source)],check=True)
    print(json.dumps({'installer':str(installer),'bytes':installer.stat().st_size}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--portable-only',action='store_true');args=parser.parse_args()
    build_portable()
    if not args.portable_only:build_installer()

