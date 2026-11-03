"""Offline Windows desktop distribution using the already cached official binaries."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from PIL import Image, ImageDraw

VERSION = '0.2.0'
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
    archive = next((Path.home()/'AppData/Local/electron/Cache').glob('*/electron-v45.1.0-win32-x64.zip'))
    if not stage.exists():
        print('Extracting cached Electron 45.1.0...', flush=True)
        stage.mkdir()
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                target = (stage / entry.filename).resolve()
                if stage.resolve() not in target.parents:
                    raise ValueError('运行环境压缩包路径无效')
            package.extractall(stage)
    app_root.mkdir(parents=True, exist_ok=True)
    for name in ('main.cjs','preload.cjs','package.json'):
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
    shutil.copyfile(bootstrap.ROOT/'data/templates.json',seed/'templates.json')
    shutil.copytree(bootstrap.ROOT/'data/thumbs',seed/'thumbs',dirs_exist_ok=True)
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
    manifest={'version':VERSION,'electron':'45.1.0','files':sum(p.is_file() for p in stage.rglob('*')),
              'bytes':sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()),
              'source_sha256':{str(p.relative_to(bootstrap.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in bootstrap.ROOT.rglob('*') if p.is_file() and ('data' not in p.relative_to(bootstrap.ROOT).parts)
                  and p.suffix in ('.py','.js','.cjs','.css','.html')}}
    (release/'desktop-build.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'portable':str(executable),'bytes':manifest['bytes']}),flush=True)


def build_installer():
    makensis=next((Path.home()/'AppData/Local/electron-builder/Cache').glob('nsis-*/nsis-*/Bin/makensis.exe'))
    installer=release/'套图工作台安装.exe'
    source=release/'installer.nsi'
    def quote(value):return str(value).replace('$','$$').replace('"','$\\"')
    lines=[
        'Unicode true', 'Name "套图工作台"', f'OutFile "{quote(installer)}"',
        'InstallDir "$LOCALAPPDATA\\Programs\\YuntuMockupWorkbench"','RequestExecutionLevel user',
        'SetCompressor zlib','!include "MUI2.nsh"',
        f'!define MUI_ICON "{quote(app_root/"app.ico")}"',
        '!insertmacro MUI_PAGE_WELCOME','!insertmacro MUI_PAGE_DIRECTORY','!insertmacro MUI_PAGE_INSTFILES',
        '!define MUI_FINISHPAGE_RUN "$INSTDIR\\套图工作台.exe"','!insertmacro MUI_PAGE_FINISH',
        '!insertmacro MUI_UNPAGE_CONFIRM','!insertmacro MUI_UNPAGE_INSTFILES','!insertmacro MUI_LANGUAGE "SimpChinese"',
        'Section "安装"','SetShellVarContext current','SetOutPath "$INSTDIR"',f'File /r "{quote(stage)}\\*"',
        'WriteUninstaller "$INSTDIR\\卸载套图工作台.exe"',
        'CreateDirectory "$SMPROGRAMS\\套图工作台"',
        'CreateShortcut "$DESKTOP\\套图工作台.lnk" "$INSTDIR\\套图工作台.exe" "" "$INSTDIR\\resources\\app\\app.ico"',
        'CreateShortcut "$SMPROGRAMS\\套图工作台\\套图工作台.lnk" "$INSTDIR\\套图工作台.exe" "" "$INSTDIR\\resources\\app\\app.ico"',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DisplayName" "套图工作台"',
        f'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DisplayVersion" "{VERSION}"',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "UninstallString" \'"$INSTDIR\\卸载套图工作台.exe"\'',
        'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\YuntuMockupWorkbench" "DisplayIcon" "$INSTDIR\\resources\\app\\app.ico"',
        'SectionEnd','Section "Uninstall"','SetShellVarContext current']
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
