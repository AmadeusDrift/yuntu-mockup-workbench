import unittest,tempfile,json
from pathlib import Path
from PIL import Image
import engine
from artwork_sources import discover
from export_naming import plan_outputs

class GenericOverviewTests(unittest.TestCase):
 def test_any_number_any_names_group_by_folder(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);group=root/'款式A';group.mkdir()
   for name in ['花朵','条纹','圆点','第四张','参考图']:
    Image.new('RGB',(16,16),'red').save(group/(name+'.png'))
   files=discover(root,exclude=['参考'])
   self.assertEqual(len(files),4)
   settings=dict(format='png',input_layout='folders',naming=dict(folder='{index:03}',image='{scene:02}',start=1))
   plans=plan_outputs([(p,0,0) for p in files],5,settings,engine.safe_name,root)
   self.assertEqual({p['folder'].split('/')[0] for p in plans.values()},{'001'})
   self.assertTrue(all(len(p['files'])==5 for p in plans.values()))
   self.assertEqual({p['folder'].split('/')[1] for p in plans.values()},{'花朵','条纹','圆点','第四张'})
 def test_selected_scene_only_renders_requested_scene(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'art.png';Image.new('RGB',(16,16),'red').save(source)
   path=root/'template.json';path.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name=str(i),width=16,height=16,ops=[]) for i in range(5)])))
   renderer=engine.Renderer(path);calls=[]
   renderer.render_scene=lambda scene,*a,**kw:(calls.append(scene['name']) or Image.new('RGBA',(16,16),'red'))
   settings=dict(width=0,height=0,fit='stretch',format='png')
   images=list(renderer.render(source,settings,scene_indices=[3]))
   self.assertEqual(calls,['3']);self.assertEqual(images[0][0],'3')
   images[0][1].close()

 def test_service_overview_cache_selection_and_export(self):
  import time
  from unittest.mock import patch
  from contextlib import nullcontext
  from service import Service
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);service=Service(root/'data');paths=[]
   for index,color in enumerate(['red','blue']):
    path=root/f'art{index}.png';Image.new('RGB',(16,16),color).save(path);paths.append(str(path))
   manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name=str(i),width=16,height=16,ops=[]) for i in range(5)])))
   service.templates=[dict(id='t',name='Template',scenes=[dict(id=str(i),name=str(i),mode='replace',targets=[]) for i in range(5)])]
   service.manifest=lambda _:manifest;service.cache.use=lambda _:nullcontext()
   settings=dict(width=0,height=0,quality=90,ppi=72,fit='stretch',format='png')
   calls=[]
   def render(renderer,scene,artwork,*args,**kwargs):
    calls.append(scene['name']);return artwork.resize((16,16)).convert('RGBA')
   def wait():
    deadline=time.time()+5
    while service.task['status']=='running' and time.time()<deadline:time.sleep(.01)
    self.assertEqual(service.task['status'],'done',service.task)
   with patch.object(engine.Renderer,'render_scene',render):
    service.overview('t',paths,settings,3);wait();self.assertEqual(calls,['3','3'])
    result=service.task['overview'];self.assertEqual(result['count'],2)
    with Image.open(service.root/result['url'].removeprefix('/media/')) as sheet:
     self.assertGreater(sheet.getpixel((176,224))[0],240)
     self.assertGreater(sheet.getpixel((512,224))[2],240)
    saved=service.save_overview(root);self.assertTrue(Path(saved['path']).is_file())
    service.overview('t',paths,settings,3);wait();self.assertEqual(len(calls),2)
    Image.new('RGB',(17,16),'green').save(paths[0])
    service.overview('t',paths,settings,3);wait();self.assertEqual(len(calls),3)
    service.overview('t',[paths[0]],settings,None);wait();self.assertEqual(service.task['overview']['count'],5)
    self.assertEqual(len(calls),7)

 def test_old_batch_discovery_stays_compatible(self):
  from parallel_batch import input_snapshot
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   for name in ['preview','art']:
    Image.new('RGB',(16,16),'red').save(root/(name+'.png'))
   old=input_snapshot(root,{'.png'},0,legacy=True)
   new=input_snapshot(root,{'.png'},0,legacy=False)
   self.assertEqual(len(old),2);self.assertEqual(len(new),1)
