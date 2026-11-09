import json
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch
from PIL import Image
import engine
from service import Service


class EditorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root / 'data')
        self.source = self.root / 'art.png'
        Image.new('RGBA', (80, 40), (100, 80, 60, 255)).save(self.source)
        self.manifest = self.root / 'template.json'
        slot = dict(kind='slot', native_size=[40, 40], quad=[[0,0],[40,0],[40,40],[0,40]])
        self.manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[
            dict(name=str(i+1),width=40,height=40,ops=[slot]) for i in range(3)])))
        self.service.templates = [dict(id='t',name='T',scenes=[dict(id=str(i),name=str(i+1),mode='replace',targets=['p']) for i in range(3)])]
        self.service.manifest = lambda _: self.manifest
        self.service.cache.use = lambda _: nullcontext()
        self.settings = dict(width=0,height=0,quality=90,ppi=72,fit='cover',format='png',orientation='original')

    def tearDown(self):
        self.temp.cleanup()

    def wait(self):
        deadline=time.monotonic()+10
        while self.service.task['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.service.task['status'],'done',self.service.task.get('error'))

    def test_partial_preview_has_original_scene_index_and_renders_only_one(self):
        calls=[]
        original=engine.Renderer.render_scene
        def render(renderer,scene,*args,**kwargs):
            calls.append(scene['name']);return original(renderer,scene,*args,**kwargs)
        with patch.object(engine.Renderer,'render_scene',render):
            self.service.preview('t',str(self.source),self.settings,scene=2);self.wait()
        self.assertEqual(calls,['3'])
        self.assertEqual(self.service.task['preview'][0]['scene_index'],2)
        self.assertEqual(self.service.task['total'],1)

    def test_invalid_scene_is_rejected_before_task(self):
        for value in (-1,3,True,'0'):
            with self.assertRaises(ValueError):self.service.preview('t',str(self.source),self.settings,scene=value)

    def test_editor_exposes_uncropped_source_and_native_layout(self):
        accepted=self.service.editor('t',str(self.source),self.settings,scene=1);self.wait()
        self.assertEqual(accepted['task_id'],self.service.task['task_id'])
        editor=self.service.task['editor']
        self.assertEqual((editor['stage_width'],editor['stage_height']),(40,40))
        self.assertEqual(editor['scene'],1)
        self.assertEqual(len(editor['affine']),6)
        with Image.open(self.service.root/editor['url'].removeprefix('/media/')) as image:
            self.assertEqual(image.size,(80,40))
        following=self.service.preview('t',str(self.source),self.settings,scene=1);self.wait()
        self.assertNotEqual(accepted['task_id'],following['task_id'])
        self.assertEqual(following['task_id'],self.service.task['task_id'])

    def test_overview_does_not_reuse_a_different_partial_scene(self):
        calls=[]
        original=engine.Renderer.render_scene
        def render(renderer,scene,*args,**kwargs):
            calls.append(scene['name']);return original(renderer,scene,*args,**kwargs)
        with patch.object(engine.Renderer,'render_scene',render):
            self.service.preview('t',str(self.source),self.settings,scene=2);self.wait()
            self.service.overview('t',[str(self.source)],self.settings,scene=0);self.wait()
            self.assertEqual(calls,['3','1'])

    def test_editor_brightness_changes_pixels_but_placement_keeps_source_geometry(self):
        self.service.editor('t',str(self.source),dict(self.settings,brightness=20,scale=70,offset_x=25),scene=0);self.wait()
        editor=self.service.task['editor']
        with Image.open(self.service.root/editor['url'].removeprefix('/media/')) as image:
            self.assertGreater(image.getpixel((0,0))[0],100)
        self.assertEqual(editor['width'],80)
        self.assertEqual(editor['x'],-20)

