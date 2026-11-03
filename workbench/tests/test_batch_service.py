import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import engine
from service import Service


class BatchTests(unittest.TestCase):
    def test_resume_rejects_same_size_corrupted_output(self):
        from parallel_batch import completed_record
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'output.jpg').write_bytes(b'bad-data')
            record={'status':'成功','outputs':['output.jpg'],'sizes':{'output.jpg':8},
                    'sha256':{'output.jpg':hashlib.sha256(b'gooddata').hexdigest()}}
            self.assertFalse(completed_record(root,record,1))

    def test_fixed_pages_encoded_once_and_reused_for_three_products(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'input';src.mkdir();out=root/'output';out.mkdir();cache=root/'cache';cache.mkdir()
            Image.new('RGBA',(40,40),(240,220,200,255)).save(cache/'fixed.png')
            scenes=[{'name':'1','width':40,'height':40,'slots':0,'fixed':True,
                     'ops':[{'kind':'static','asset':'fixed.png'}]},
                    {'name':'2','width':40,'height':40,'slots':1,
                     'ops':[{'kind':'slot','native_size':[40,40],'quad':[[0,0],[40,0],[40,40],[0,40]],'opacity':255}]}]
            manifest=cache/'template.json';manifest.write_text(json.dumps({'version':4,'workbench':True,'scenes':scenes}))
            for n,color in [(10,'red'),(2,'blue'),(1,'green')]:Image.new('RGBA',(40,40),color).save(src/f'SKU-{n}.png')
            settings=engine.settings_checked(dict(width=40,height=40,ppi=72,quality=95,fit='contain',format='jpg'))
            original=Image.Image.save;encoded=[]
            def save(im,path,*args,**kwargs):
                if Path(path).name=='fixed_01.jpg':encoded.append(str(path))
                return original(im,path,*args,**kwargs)
            with patch.object(Image.Image,'save',save):
                dest,state=engine.batch(manifest,src,out,settings)
            self.assertEqual(len(encoded),1)
            self.assertEqual([r[0] for r in state['identity']['files']],['SKU-1.png','SKU-2.png','SKU-10.png'])
            pages=[(dest/f'SKU-{n}'/f'SKU-{n}_01.jpg').read_bytes() for n in (1,2,10)]
            self.assertTrue(all(page==pages[0] for page in pages))
            before={p:p.stat().st_mtime_ns for p in dest.glob('*/*.jpg')}
            engine.batch(manifest,src,out,settings,resume=dest)
            self.assertTrue(all(p.stat().st_mtime_ns==mtime for p,mtime in before.items()))


class ServiceTests(unittest.TestCase):
    def test_history_save_failure_always_releases_busy_state(self):
        with tempfile.TemporaryDirectory() as td:
            app=Service(Path(td));app.history=[{'status':'已完成'}]
            with patch.object(app,'save',side_effect=OSError('disk full')):
                app.start('test',lambda:None)
                for _ in range(100):
                    if app.task['status'] not in ('running','paused','stopping'):break
                    time.sleep(.01)
                self.assertEqual(app.task['status'],'failed')
                self.assertIsNone(app.control)
                self.assertIn('disk full',app.task['error'])

    def test_interrupted_batch_already_appears_in_resumable_history(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'input';src.mkdir();out=root/'output';out.mkdir()
            Image.new('RGB',(20,20),'red').save(src/'SKU.png')
            app=Service(root/'data');app.templates=[{'id':'a','name':'Template','digest':'x','scenes':[{'id':'s','mode':'fixed','targets':[],'candidates':[]}]}]
            manifest=app.cache.root/('a'*64)/'template.json';manifest.parent.mkdir();manifest.write_text('{}')
            app.manifest=lambda template:manifest
            def interrupted(*args,**kwargs):
                dest=out/'interrupted';dest.mkdir()
                kwargs['progress'](0,1,'','运行中',str(dest))
                raise OSError('disk unavailable')
            with patch.object(engine,'batch',interrupted):
                app.batch(['a'],src,out,dict(width=20,height=20,ppi=72,quality=95,fit='contain',format='jpg'))
                for _ in range(200):
                    if app.task['status']=='failed':break
                    time.sleep(.01)
            loaded=Service(root/'data')
            self.assertEqual(len(loaded.history),1)
            self.assertEqual(loaded.history[0]['template_ids'],['a'])

    def test_one_task_at_a_time_and_failure_visible(self):
        with tempfile.TemporaryDirectory() as td:
            app=Service(Path(td));gate=threading.Event()
            app.start('test',lambda:gate.wait(2))
            with self.assertRaises(ValueError):app.start('test',lambda:None)
            with self.assertRaises(ValueError):app.cache_command('clean')
            gate.set()
            for _ in range(100):
                if app.state()['task']['status']=='done':break
                time.sleep(.01)
            self.assertEqual(app.state()['task']['status'],'done')
            def fail():raise ValueError('bad layer')
            app.start('test',fail)
            for _ in range(100):
                if app.state()['task']['status']=='failed':break
                time.sleep(.01)
            self.assertEqual(app.state()['task']['error'],'bad layer')

    def test_configuration_persists_and_unknown_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            app=Service(Path(td));app.templates=[{'id':'a','scenes':[{'id':'s','mode':'replace','targets':['x'],'candidates':[{'id':'x'}]}]}]
            app.configure('a',[{'id':'s','mode':'fixed','targets':['x']}])
            loaded=Service(Path(td))
            self.assertEqual(loaded.templates[0]['scenes'][0]['mode'],'fixed')
            with self.assertRaises(ValueError):app.configure('a',[{'id':'s','mode':'replace','targets':['z']}])
