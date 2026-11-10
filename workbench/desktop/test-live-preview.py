"""Run the real desktop against a private 43-artwork/three-scene fixture."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'workbench'))
import bootstrap
import catalog
from cache import cache_key
from PIL import Image, ImageDraw
sys.path.insert(0, str(root / 'workbench/desktop'))
import installer_helper

version = json.loads((root / 'workbench/desktop/package.json').read_text(encoding='utf-8'))['version']
target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / ('release/套图工作台-v' + version)
assert (target / '套图工作台.exe').is_file(), target
run = root / '.analysis-temp' / ('live-preview-desktop-' + time.strftime('%Y%m%d-%H%M%S'))
run.mkdir()
data, source, output = (run / part for part in ('data', 'input', 'output'))
for folder in (data, source, output):folder.mkdir()
for number in range(43):
    image = Image.new('RGBA', (360,240), ((number*29)%220+20, (number*41)%220+20, (number*67)%220+20, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0,0,119,239), fill=(220,50,65,255))
    draw.rectangle((240,0,359,239), fill=(40,70,220,255))
    draw.text((170,110), str(number+1), fill='white')
    image.save(source / f'印花{number+1:03d}.png')
    image.close()
psd = run / 'fixture.psd'
psd.write_bytes(b'Synthetic compiled preview fixture. Source files are never changed by tests.')
signature = catalog.fingerprint(psd)
scenes = [dict(id=str(i), name=str(i+1), mode='replace', targets=['a'],
               candidates=[dict(id='a',name='print',path='print')], thumbnail=None) for i in range(3)]
template = dict(id='fixture',name='实时预览测试',path=str(psd),digest=signature['sha256'],signature=signature,
                canvas=[1200,800],scenes=scenes,warnings=[])
(data/'templates.json').write_text(json.dumps([template]),encoding='utf-8')
settings = dict(width=0,height=0,ppi=72,quality=100,fit='cover',format='png',trim=False,fit_policy=2,
                orientation='original',rotation=0,brightness=0,scale=100,offset_x=0,offset_y=0,
                input_layout='folders',input_exclude=[],naming=dict(folder='{name}',image='{scene:02}',start=1))
(data/'preferences.json').write_text(json.dumps(dict(input=str(source),output=str(output),settings=settings,
                                                    limit=0,preferences_version=2)),encoding='utf-8')
folder = data / 'cache' / cache_key(template)
folder.mkdir(parents=True)
Image.new('RGBA',(1200,800),'#e8eef6').save(folder/'background.png')
ops = [dict(kind='static',asset='background.png'),
       dict(kind='slot',native_size=[600,600],quad=[[230,100],[970,140],[930,720],[260,680]],opacity=255)]
(folder/'template.json').write_text(json.dumps(dict(version=4,workbench=True,scenes=[
    dict(name=str(i+1),width=1200,height=800,slots=1,ops=ops) for i in range(3)])),encoding='utf-8')
(folder/'cache-meta.json').write_text(json.dumps(dict(name='fixture',last_used=time.time())),encoding='utf-8')
env = dict(os.environ, PYTHONIOENCODING='utf-8', TEMP=str(run), TMP=str(run))
env.pop('ELECTRON_RUN_AS_NODE',None)
node = Path.home() / 'AppData/Local/Programs/node-v24.21.0-win-x64/node.exe'
with (run/'desktop.log').open('wb') as log:
    process = subprocess.Popen([str(target/'套图工作台.exe'),'--data-dir='+str(data),'--remote-debugging-port=9344'],
                               env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+40
        while not (data/'desktop-status.json').exists():
            if process.poll() is not None:raise RuntimeError('Desktop exited; see '+str(run/'desktop.log'))
            if time.monotonic()>deadline:raise TimeoutError('Desktop startup timed out')
            time.sleep(.2)
        checked=subprocess.run([str(node),str(root/'workbench/desktop/test-live-preview.cjs'),str(data),str(run)],
                               env=env,timeout=240,creationflags=subprocess.CREATE_NO_WINDOW,
                               stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8')
        (run/'node-test.log').write_text(checked.stdout,encoding='utf-8')
        print(checked.stdout,flush=True)
        checked.check_returncode()
        result=json.loads((run/'ui-result.json').read_text(encoding='utf-8'))
        report=dict(status='PASS',version=version,run=str(run),artworks=43,scenes=3,**result)
        (run/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PASS: '+str(run),flush=True)
    finally:
        installer_helper.close_applications(target,data,90)
        process.wait(timeout=15)
