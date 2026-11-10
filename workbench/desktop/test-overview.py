import json,os,sys,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'workbench'))
import bootstrap,catalog
from cache import cache_key
from PIL import Image
sys.path.insert(0,str(root/'workbench/desktop'));import installer_helper
run=root/'.analysis-temp'/('overview-desktop-'+time.strftime('%Y%m%d-%H%M%S'));run.mkdir()
data=run/'data';data.mkdir();src=run/'input';src.mkdir();output=run/'output';output.mkdir()
for name,colors in [('款式A',['red','green','blue','purple']),('款式B',['orange','cyan'])]:
 folder=src/name;folder.mkdir()
 for i,color in enumerate(colors):Image.new('RGB',(20,20),color).save(folder/(f'印花{i+1}.png'))
 Image.new('RGB',(20,20),'yellow').save(folder/'参考图.png')
psd=run/'fixture.psd';psd.write_bytes(b'Synthetic cached renderer fixture; not a PSD import test.')
sig=catalog.fingerprint(psd)
scenes=[dict(id=str(i),name=str(i+1),mode='replace',targets=['a'],candidates=[dict(id='a',name='print',path='print')],thumbnail=None) for i in range(5)]
template=dict(id='fixture',name='通用五场景测试',path=str(psd),digest=sig['sha256'],signature=sig,canvas=[20,20],scenes=scenes,warnings=[])
(data/'templates.json').write_text(json.dumps([template]),encoding='utf-8')
settings=dict(width=0,height=0,ppi=72,quality=95,fit='stretch',format='png',trim=False,fit_policy=2,orientation='original',rotation=0,input_layout='folders',input_exclude=['参考'],naming=dict(folder='{name}',image='{scene:02}',start=1))
(data/'preferences.json').write_text(json.dumps(dict(input=str(src),output=str(output),settings=settings,limit=0,preferences_version=2)),encoding='utf-8')
cache=data/'cache'/cache_key(template);cache.mkdir(parents=True)
ops=[dict(kind='slot',native_size=[20,20],quad=[[0,0],[20,0],[20,20],[0,20]],opacity=255)]
(cache/'template.json').write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name=str(i+1),width=20,height=20,slots=1,ops=ops) for i in range(5)])),encoding='utf-8')
(cache/'cache-meta.json').write_text(json.dumps(dict(name='fixture',last_used=time.time())),encoding='utf-8')
version=json.loads((root/'workbench/desktop/package.json').read_text(encoding='utf-8'))['version']
target=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/('release/套图工作台-v'+version)
env=dict(os.environ);env.pop('ELECTRON_RUN_AS_NODE',None)
log=(run/'desktop.log').open('wb');process=subprocess.Popen([str(target/'套图工作台.exe'),'--data-dir='+str(data),'--remote-debugging-port=9342'],env=env,stdout=log,stderr=log)
try:
 for _ in range(150):
  if (data/'desktop-status.json').exists():break
  if process.poll() is not None:raise RuntimeError('Desktop exited')
  time.sleep(.2)
 checked=subprocess.run([r'C:/Users/12079/AppData/Local/Programs/node-v24.21.0-win-x64/node.exe',str(root/'workbench/desktop/test-overview.cjs'),str(data),str(run)],timeout=90,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8')
 (run/'node-test.log').write_text(checked.stdout,encoding='utf-8');print(checked.stdout,flush=True);checked.check_returncode()
 result=json.loads((run/'ui-result.json').read_text(encoding='utf-8'))
 for product,colors in [('款式A',[(255,0,0),(0,128,0),(0,0,255),(128,0,128)]),('款式B',[(255,165,0),(0,255,255)])]:
  for i,color in enumerate(colors):
   files=sorted((Path(result['output'])/product/f'印花{i+1}').glob('*.png'));assert len(files)==5
   for file in files:
    with Image.open(file) as image:assert image.getpixel((10,10))[:3]==color
 assert len(list(Path(result['output']).rglob('*.png')))==30
 with Image.open(result['saved']) as sheet:assert sheet.width>500 and sheet.height>300
 (run/'verification.json').write_text(json.dumps(dict(status='PASS',version=version,run=str(run),selected_artworks=2,selected_scene='3',set_scenes=5,exported_images=30,arbitrary_group_sizes=[4,2],fixture='synthetic cached renderer'),ensure_ascii=False,indent=2),encoding='utf-8')
 print('PASS: independent generic groups and overview export')
finally:
 installer_helper.close_applications(target,data,30);process.wait(timeout=15);log.close()
