import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import engine


class DraftRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        size = (1600, 800)
        background = Image.new('RGBA', size, (30, 40, 50, 255))
        background.save(self.root / 'background.png')
        mask = Image.new('L', size, 0)
        ImageDraw.Draw(mask).rectangle((200,100,1400,700), fill=255)
        mask.save(self.root / 'mask.png')
        self.source = self.root / 'art.png'
        art = Image.new('RGBA', (400, 200), (90, 140, 60, 255))
        ImageDraw.Draw(art).rectangle((0,0,199,199), fill=(220,30,90,255))
        art.save(self.source)
        self.slot = dict(kind='slot', native_size=[400,200], quad=[[200,100],[1400,100],[1400,700],[200,700]], mask='mask.png')
        self.data = dict(version=4, workbench=True, scenes=[dict(name='front', width=1600, height=800,
            ops=[dict(kind='static', asset='background.png'), self.slot])])
        self.manifest = self.root / 'template.json'
        self.settings = dict(width=0, height=0, fit='cover', format='png', scale=70, offset_x=12, offset_y=-8,
                             orientation='original', brightness=18)

    def tearDown(self):
        self.temp.cleanup()

    def renderer(self):
        self.manifest.write_text(json.dumps(self.data))
        return engine.Renderer(self.manifest)

    def draft(self, renderer):
        self.assertTrue(callable(getattr(renderer, 'scaled_preview', None)), 'Drafts must downscale before compositing')
        return renderer.scaled_preview(400)

    def render(self, renderer):
        iterator = renderer.render(self.source, self.settings, scene_indices=[0])
        try:
            return next(iterator)[1]
        finally:
            iterator.close()

    def test_draft_scales_geometry_assets_and_keeps_exact_renderer_unchanged(self):
        full = self.renderer()
        before_data = copy.deepcopy(full.data)
        before = self.render(full)
        draft = self.draft(full)
        self.assertEqual(draft.assets['background.png'].size, (400,200))
        self.assertEqual(draft.data['scenes'][0]['ops'][1]['native_size'], [100,50])
        small = self.render(draft)
        self.assertEqual(small.size, (400,200))
        reference = before.resize(small.size, Image.Resampling.LANCZOS)
        # Proxy sampling may differ at edges; composition/placement should agree throughout the image.
        self.assertLess(np.abs(np.asarray(small).astype(float)-np.asarray(reference)).mean(), 2.0)
        after = self.render(full)
        self.assertEqual(full.data, before_data)
        self.assertEqual(after.tobytes(), before.tobytes())
        for image in (before, small, reference, after):image.close()

    def test_draft_keeps_transparency_and_brightness(self):
        self.data['scenes'][0]['ops'] = [self.slot]
        renderer = self.draft(self.renderer())
        image = self.render(renderer)
        self.assertEqual(image.getpixel((0,0))[3], 0)
        self.assertGreater(image.getpixel((200,100))[0], 90)
        self.assertEqual(image.getpixel((200,100))[3], 255)
        image.close()

    def test_auto_orientation_uses_original_print_aspect_near_square_threshold(self):
        self.slot['native_size'] = [401,364]
        art = Image.new('RGBA',(200,400),'#dd2244')
        ImageDraw.Draw(art).rectangle((0,200,199,399),fill='#2255dd')
        art.save(self.source);art.close()
        self.settings.update(fit='auto',orientation='auto',scale=100,offset_x=0,offset_y=0,brightness=0)
        full=self.renderer()
        for edge in (400,720):
            with self.subTest(edge=edge):
                draft=full.scaled_preview(edge)
                a,b=self.render(full),self.render(draft)
                down=a.resize(b.size,Image.Resampling.LANCZOS)
                self.assertLess(np.abs(np.asarray(b).astype(float)-np.asarray(down)).mean(),3.0,
                                'Rounding the draft print area must not turn the artwork by 90 degrees')
                for image in (a,b,down):image.close()

    def test_draft_rebuilds_static_mask_and_shadow_at_preview_scale(self):
        layer = Image.new('RGBA', (1600,800), (0,0,0,0))
        ImageDraw.Draw(layer).rectangle((700,300,900,500), fill=(180,100,50,255))
        layer.save(self.root / 'layer.png')
        shadow = dict(dx=40,dy=20,blur=20,spread=0,color=[0,0,0],opacity=40)
        self.data['scenes'][0]['ops'].append(dict(kind='static', asset='layer.png', mask='mask.png', shadow=shadow, opacity=200))
        full = self.renderer()
        draft = self.draft(full)
        self.assertEqual(draft.data['scenes'][0]['ops'][-1]['shadow']['dx'], 10)
        a, b = self.render(full), self.render(draft)
        down = a.resize(b.size, Image.Resampling.LANCZOS)
        self.assertLess(np.abs(np.asarray(b).astype(float)-np.asarray(down)).mean(), 2.0)
        for image in (a,b,down):image.close()

    def test_draft_resamples_normalized_warp_without_scaling_uv_values(self):
        from quilt_warp import ALGORITHM_VERSION, save_map
        width, height = 1600, 800
        u = np.tile(np.rint((np.arange(width)+.5)*65535/width).astype(np.uint16), (height,1))
        v = np.tile(np.rint((np.arange(height)+.5)*65535/height).astype(np.uint16)[:,None], (1,width))
        mapping = dict(u=u, v=v, coverage=np.full((height,width),255,np.uint8),
                       metadata=dict(algorithm=ALGORITHM_VERSION, project_controls_first=True, canvas=[width,height],
                                     native=[400,200], coverage_bbox=[0,0,width,height]))
        save_map(self.root / 'warp.npz', mapping)
        self.slot.update(warp='warp.npz', quad=[[0,0],[width,0],[width,height],[0,height]])
        self.slot.pop('mask')
        full = self.renderer()
        draft = self.draft(full)
        self.assertEqual(draft.warp_maps['warp.npz']['u'].shape, (200,400))
        self.assertGreater(int(draft.warp_maps['warp.npz']['u'].max()), 65000)
        a, b = self.render(full), self.render(draft)
        down = a.resize(b.size, Image.Resampling.LANCZOS)
        self.assertLess(np.abs(np.asarray(b).astype(float)-np.asarray(down)).mean(), 3.0)
        self.assertEqual(full.warp_maps['warp.npz']['u'].shape, (800,1600))
        for image in (a,b,down):image.close()
