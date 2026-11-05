"""Generic render cost bounds and byte-exact compatibility with the v0.2.2 kernel."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import blend_modes
import engine

spec = importlib.util.spec_from_file_location('blend_v022_reference', Path(__file__).parent / 'fixtures' / 'blend_v022.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class RenderOptimizationTests(unittest.TestCase):
    def test_clip_regions_keep_legacy_adjustments_masks_and_dynamic_base_pixels(self):
        spec=importlib.util.spec_from_file_location('engine_v022_regions_reference',Path(__file__).parent/'fixtures'/'engine_v022.py')
        previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
        rng=np.random.default_rng(314159)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);size=(31,27)
            b=rng.integers(0,256,(27,31,4),dtype=np.uint8);b[:,:,3]=0;b[5:20,8:23,3]=rng.integers(0,256,(15,15),dtype=np.uint8)
            Image.fromarray(b).save(root/'base.png')
            Image.fromarray(rng.integers(0,256,(27,31,4),dtype=np.uint8)).save(root/'paint.png')
            Image.fromarray(rng.integers(0,256,(27,31),dtype=np.uint8)).save(root/'mask.png')
            art=Image.fromarray(b)
            for version in (1,2,3,4):
                for dynamic in (False,True):
                    for mode in ('normal','multiply','screen','soft_light'):
                        ops=[dict(kind='static',asset='paint.png',mask='mask.png',opacity=193,blend=mode,blend_if=[10,30,220,245]),
                             dict(kind='adjustment',lut=[min(255,i+7) for i in range(256)],mask='mask.png',opacity=141)]
                        clip=dict(kind='clipstack',ops=ops)
                        if dynamic:clip['base_op']=dict(kind='slot',native_size=list(size),quad=[[0,0],[31,0],[31,27],[0,27]],opacity=255)
                        else:clip['base']='base.png'
                        manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=version,workbench=True,scenes=[dict(name='scene',width=31,height=27,ops=[clip])])))
                        current,prior=engine.Renderer(manifest),previous.Renderer(manifest)
                        with patch.object(blend_modes,'composite_rgba',reference.composite_rgba):
                            expected=prior.render_scene(prior.data['scenes'][0],art,'stretch',{}).tobytes()
                        self.assertEqual(current.render_scene(current.data['scenes'][0],art,'stretch',{}).tobytes(),expected,(version,dynamic,mode))

    def test_clipped_effects_only_compute_inside_base_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = Image.new('RGBA', (800, 600)); base.paste((40, 90, 160, 180), (250, 200, 270, 225)); base.save(root/'base.png')
            Image.new('RGBA', base.size, (190, 130, 70, 150)).save(root/'paint.png')
            ops = [dict(kind='clipstack', base='base.png', ops=[dict(kind='static', asset='paint.png', blend='soft_light'),dict(kind='static',asset='paint.png',blend='screen')])]
            manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name='scene',width=800,height=600,ops=ops)])))
            renderer=engine.Renderer(manifest);original=np.asarray;pixels=[]
            def array(image,*args,**kwargs):
                if isinstance(image,Image.Image):pixels.append(image.width*image.height)
                return original(image,*args,**kwargs)
            with patch.object(blend_modes.np,'asarray',array):
                result=renderer.render_scene(renderer.data['scenes'][0],Image.new('RGBA',base.size),'auto',{})
            self.assertLessEqual(sum(pixels),4*20*25)
            spec=importlib.util.spec_from_file_location('engine_v022_clip_reference',Path(__file__).parent/'fixtures'/'engine_v022.py')
            previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous);prior=previous.Renderer(manifest)
            with patch.object(blend_modes,'composite_rgba',reference.composite_rgba):
                expected=prior.render_scene(prior.data['scenes'][0],Image.new('RGBA',base.size),'auto',{})
            self.assertEqual(result.tobytes(),expected.tobytes())

    def test_sparse_blends_do_not_allocate_full_canvas_numpy_inputs(self):
        backdrop = Image.new('RGBA', (800, 600), (30, 60, 90, 200))
        source = Image.new('RGBA', backdrop.size, (180, 40, 230, 0))
        source.paste((220, 100, 40, 140), (210, 150, 260, 220))
        original = np.asarray
        for mode in ('multiply', 'screen', 'soft_light'):
            pixels = []
            def array(image, *args, **kwargs):
                if isinstance(image, Image.Image): pixels.append(image.width * image.height)
                return original(image, *args, **kwargs)
            with patch.object(blend_modes.np, 'asarray', array):
                result = blend_modes.composite_rgba(backdrop, source, mode)
            self.assertLessEqual(sum(pixels), 2 * 50 * 70, mode)
            self.assertEqual(result.tobytes(), reference.composite_rgba(backdrop, source, mode).tobytes())

    def test_all_modes_match_prior_pixels_across_sizes_alpha_and_coverage(self):
        rng = np.random.default_rng(271828)
        for size in ((1, 1), (9, 7), (113, 137)):
            w, h = size
            base = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
            paint = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
            for alpha in ('mixed', 'opaque', 'transparent'):
                b = base.copy()
                if alpha != 'mixed': b[:, :, 3] = 255 if alpha == 'opaque' else 0
                else: b[::2, ::2, 3] = 0  # Hidden RGB must retain the previous kernel's convention.
                for coverage in ('full', 'small', 'empty', 'corners'):
                    s = paint.copy()
                    if coverage == 'small':
                        s[:, :, 3] = 0; s[h//3:2*h//3+1, w//3:2*w//3+1, 3] = 127
                    elif coverage == 'empty': s[:, :, 3] = 0
                    elif coverage == 'corners':
                        s[:, :, 3] = 0; s[0, 0, 3] = 1; s[-1, -1, 3] = 254
                    backdrop, source = Image.fromarray(b), Image.fromarray(s)
                    for mode in ('normal', 'multiply', 'screen', 'soft_light'):
                        with self.subTest(size=size, alpha=alpha, coverage=coverage, mode=mode):
                            before = backdrop.tobytes(), source.tobytes()
                            out = blend_modes.composite_rgba(backdrop, source, mode, chunk_rows=11)
                            self.assertEqual(out.tobytes(), reference.composite_rgba(backdrop, source, mode, chunk_rows=11).tobytes())
                            self.assertEqual((backdrop.tobytes(), source.tobytes()), before)

    def test_non_rgba_images_keep_prior_conversion_behavior(self):
        for back_mode in ('RGB', 'L', 'P', 'RGBA'):
            for source_mode in ('RGB', 'L', 'P', 'RGBA'):
                backdrop = Image.new('RGB', (19, 23), (90, 150, 70)).convert(back_mode)
                source = Image.new('RGB', (19, 23), (200, 40, 160)).convert(source_mode)
                for mode in ('normal', 'multiply', 'screen', 'soft_light'):
                    self.assertEqual(blend_modes.composite_rgba(backdrop, source, mode).tobytes(), reference.composite_rgba(backdrop, source, mode).tobytes())

    def test_static_masks_opacity_and_shadows_are_prepared_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            Image.new('RGBA', (48, 48), (100, 140, 190, 180)).save(root / 'static.png')
            mask = Image.new('L', (48, 48)); mask.paste(210, (8, 9, 36, 35)); mask.save(root / 'mask.png')
            op = dict(kind='static', asset='static.png', mask='mask.png', opacity=135,
                      shadow=dict(blur=5, spread=1, dx=2, dy=3, opacity=30, color=[0, 0, 0]))
            manifest = root / 'template.json'
            manifest.write_text(json.dumps(dict(version=4, workbench=True, scenes=[dict(name='scene', width=48, height=48, ops=[op])])))
            with patch.object(engine, 'apply_opacity', wraps=engine.apply_opacity) as opacity, patch.object(engine, 'add_shadow', wraps=engine.add_shadow) as shadow:
                renderer = engine.Renderer(manifest)
                initial = opacity.call_count, shadow.call_count
                expected = None
                for _ in range(3):
                    image = renderer.render_scene(renderer.data['scenes'][0], Image.new('RGBA', (48, 48)), 'auto', {})
                    if expected is None: expected = image.tobytes()
                    self.assertEqual(image.tobytes(), expected)
                self.assertEqual((opacity.call_count, shadow.call_count), initial)

    def test_prepared_static_effects_match_original_and_are_safe_to_share(self):
        from concurrent.futures import ThreadPoolExecutor
        from bootstrap import PORTABLE
        spec = importlib.util.spec_from_file_location('original_static_reference', PORTABLE / 'engine.py')
        original = importlib.util.module_from_spec(spec); spec.loader.exec_module(original)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            Image.new('RGBA', (48, 48), (100, 140, 190, 180)).save(root / 'static.png')
            for opacity in (0, 135, 255):
                for mask_fill in (0, 128, 255):
                    mask = Image.new('L', (48, 48)); mask.paste(mask_fill, (8, 9, 36, 35)); mask.save(root / 'mask.png')
                    op = dict(kind='static', asset='static.png', mask='mask.png', opacity=opacity,
                              shadow=dict(blur=5, spread=1, dx=2, dy=3, opacity=30, color=[10, 20, 30]))
                    manifest = root / 'template.json'
                    manifest.write_text(json.dumps(dict(version=4, workbench=True, scenes=[dict(name='scene', width=48, height=48, ops=[op])])))
                    renderer, prior = engine.Renderer(manifest), original.Renderer(manifest)
                    art = Image.new('RGBA', (48, 48))
                    expected = prior.render_scene(prior.data['scenes'][0], art, 'contain', {}).tobytes()
                    before = {name: image.tobytes() for name, image in renderer.assets.items()}
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        results = list(pool.map(lambda _: renderer.render_scene(renderer.data['scenes'][0], art, 'contain', {}).tobytes(), range(8)))
                    self.assertTrue(all(result == expected for result in results))
                    self.assertEqual(before, {name: image.tobytes() for name, image in renderer.assets.items()})
