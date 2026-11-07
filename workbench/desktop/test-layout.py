import json,os,sys,subprocess,time,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'workbench'))
import bootstrap,catalog
from cache import cache_key
from PIL import Image
sys.path.insert(0,str(root/'workbench/desktop'));import installer_helper
run=root/'.analysis-temp'/('layout-desktop-'+time.strftime('%Y%m%d-%H%M%S'));run.mkdir()
data=run/'data';data.mkdir();src=run/'input';src.mkdir();output=run/'output';output.mkdir()
for name in ['002','010']:
 folder=src/name;folder.mkdir()
 for name,color in [('左','red'),('中','green'),('右','blue')]:Image.new('RGB',(20,20),color).save(folder/(name+'.png'))
 Image.new('RGB',(60,20),'yellow').save(folder/'三联预览.png')
psd=run/'synthetic-renderer-fixture.psd';psd.write_bytes(b'Synthetic cached renderer fixture, not a PSD import test.')
sig=catalog.fingerprint(psd)
scene=dict(id='s',name='1',mode='replace',targets=['a','b','c'],candidates=[dict(id=i,name=n,path=n,linked=False) for i,n in [('a','左画框'),('b','中画框'),('c','右画框')]],thumbnail=None)
template=dict(id='fixture',name='三联画测试',path=str(psd),digest=sig['sha256'],signature=sig,canvas=[60,20],scenes=[dict(scene,id=str(i),name=str(i)) for i in range(1,8)],warnings=[])
import shutil
thumbs=root/'workbench/data/thumbs'
if thumbs.exists():shutil.copytree(thumbs,data/'thumbs')
real=json.loads((root/'workbench/data/templates.json').read_text(encoding='utf-8')) if (root/'workbench/data/templates.json').exists() else [template]
for i,item in enumerate(template['scenes']):item['thumbnail']=real[i%len(real)]['scenes'][0].get('thumbnail')
(data/'templates.json').write_text(json.dumps([dict(template,id=f'fixture-{i}',name=f'{i:02} 测试模板') for i in range(16)]),encoding='utf-8')
settings=dict(width=0,height=0,ppi=72,quality=95,fit='stretch',format='png',trim=False,fit_policy=2,orientation='original',rotation=0,naming=dict(folder='{name}',image='{scene:02}',start=1))
(data/'preferences.json').write_text(json.dumps(dict(input=str(src),output=str(output),settings=settings,limit=0,preferences_version=2)),encoding='utf-8')

cache=data/'cache'/cache_key(template);cache.mkdir(parents=True)
ops=[dict(kind='slot',artwork_role=role,native_size=[20,20],quad=[[i*20,0],[(i+1)*20,0],[(i+1)*20,20],[i*20,20]],opacity=255) for i,role in enumerate(['left','center','right'])]
(cache/'template.json').write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name=str(i),width=60,height=20,slots=3,ops=ops) for i in range(1,8)])),encoding='utf-8')
(cache/'cache-meta.json').write_text(json.dumps(dict(name='fixture',last_used=time.time())),encoding='utf-8')
target=root/'release/套图工作台-v0.3.4';env=dict(os.environ);env.pop('ELECTRON_RUN_AS_NODE',None)
log=(run/'desktop.log').open('wb');process=subprocess.Popen([str(target/'套图工作台.exe'),'--data-dir='+str(data),'--remote-debugging-port=9342'],env=env,stdout=log,stderr=log)
try:
 for _ in range(150):
  if (data/'desktop-status.json').exists():break
  if process.poll() is not None:raise RuntimeError('Desktop exited')
  time.sleep(.2)
 subprocess.run([r'C:\Users\12079\AppData\Local\Programs\node-v24.21.0-win-x64\node.exe',str(root/'workbench/desktop/test-layout.cjs'),str(data),str(run),str(target/'套图工作台.exe')],check=True,timeout=90)
 print('PASS: template layout and selection stability')
finally:
 installer_helper.close_applications(target,data,30);process.wait(timeout=15);log.close()
