"""Compositor equations, dynamic clipping and unchanged legacy cache pixels."""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
PORTABLE = ROOT / '通用PSD高速套图_正片叠底_2套并行便携版'
sys.path.insert(0, str(PORTABLE / 'vendor'))
sys.path.insert(0, str(ROOT / 'workbench' / 'core'))
from PIL import Image
import engine
from blend_modes import composite_rgba
from psd_tools.constants import BlendMode, Tag


def reference_pixel(backdrop, source, mode):
    ab, a = backdrop[3] / 255, source[3] / 255
    alpha = a + ab * (1 - a)
    result = []
    for cb, cs in zip(backdrop[:3], source[:3]):
        b, s = cb / 255, cs / 255
        if mode == 'screen':
            blend = b + s - b * s
        else:
            d = ((16 * b - 12) * b + 4) * b if b <= .25 else math.sqrt(b)
            blend = b - (1 - 2 * s) * b * (1 - b) if s <= .5 else b + (2 * s - 1) * (d - b)
        premult = (1 - a) * ab * b + (1 - ab) * a * s + a * ab * blend
        result.append(round(255 * premult / alpha) if alpha else 0)
    return tuple(result + [round(alpha * 255)])


class Blocks(dict):
    def get_data(self, key, default=None):
        return self.get(key, default)


class Layer:
    def __init__(self, name, kind='pixel', mode=BlendMode.NORMAL, clipping=False, opacity=255):
        self.name, self.kind, self.blend_mode = name, kind, mode
        self.clipping, self.opacity, self.fill_opacity = clipping, opacity, 255
        self.visible = True
        self.left = self.top = 0
        self.width = self.height = 4
        self.tagged_blocks = Blocks()
        self._record = SimpleNamespace(blending_ranges=None)
        if kind == 'smartobject':
            self.tagged_blocks[Tag.SMART_OBJECT_LAYER_DATA1] = SimpleNamespace(data={
                b'Sz  ': {b'Wdth': 4, b'Hght': 4}, b'Trnf': [0, 0, 4, 0, 4, 4, 0, 4]})

    def is_group(self): return False
    def has_effects(self): return False
    def has_mask(self): return False
    def has_vector_mask(self): return False
    def topil(self): return Image.new('RGBA', (4, 4), (200, 150, 100, 128))


class EffectsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.analysis-temp').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='effects-', dir=ROOT / '.analysis-temp')
        self.folder = Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def renderer(self, ops, version=4, workbench=False, size=(4, 4)):
        data = {'version': version, 'scenes': [{'name': 'test', 'width': size[0], 'height': size[1], 'ops': ops}]}
        if workbench: data['workbench'] = True
        manifest = self.folder / 'template.json'
        manifest.write_text(json.dumps(data), encoding='utf8')
        return engine.Renderer(manifest)

    def test_screen_and_soft_light_match_independent_alpha_equations(self):
        pairs = [((18, 64, 230, 255), (240, 130, 20, 255)),
                 ((32, 90, 200, 86), (170, 40, 220, 143)),
                 ((200, 10, 150, 0), (10, 210, 100, 155)),
                 ((17, 200, 99, 123), (210, 80, 33, 0))]
        for mode in ('screen', 'soft_light'):
            for b, s in pairs:
                with self.subTest(mode=mode, b=b, s=s):
                    back, src = Image.new('RGBA', (2, 3), b), Image.new('RGBA', (2, 3), s)
                    out = composite_rgba(back, src, mode, chunk_rows=1)
                    self.assertEqual(out.getpixel((0, 2)), reference_pixel(b, s, mode))
                    self.assertEqual(back.getpixel((0, 0)), b)
                    self.assertEqual(src.getpixel((0, 0)), s)

    def test_compiler_preserves_effect_modes_as_separate_operations(self):
        compiler = engine.Compiler(self.folder, 'PRODUCT')
        ops = compiler.compile_layers([Layer('background'), Layer('shine', mode=BlendMode.SCREEN),
                                       Layer('shade', mode=BlendMode.SOFT_LIGHT)], (4, 4), 'test')
        self.assertEqual([op.get('blend', 'normal') for op in ops], ['normal', 'screen', 'soft_light'])

    def test_dynamic_product_base_masks_fixed_clipped_effects(self):
        compiler = engine.Compiler(self.folder, 'PRODUCT')
        ops = compiler.compile_layers([Layer('PRODUCT', kind='smartobject'),
                                       Layer('shine', mode=BlendMode.SCREEN, clipping=True)], (4, 4), 'test')
        self.assertEqual(ops[0]['kind'], 'clipstack')
        self.assertEqual(ops[0]['base_op']['kind'], 'slot')
        self.assertEqual(engine.slot_count(ops), 1)
        art = Image.new('RGBA', (4, 4))
        art.putpixel((1, 1), (40, 80, 120, 128))
        renderer = self.renderer(ops, workbench=True)
        out = renderer.render_scene(renderer.data['scenes'][0], art, 'stretch', {})
        self.assertEqual(out.getchannel('A').tobytes(), art.getchannel('A').tobytes())
        self.assertGreater(out.getpixel((1, 1))[0], 40)
        self.assertEqual(out.getpixel((0, 0))[3], 0)

    def test_workbench_contain_leaves_padding_transparent_and_legacy_stays_white(self):
        slot = {'kind': 'slot', 'native_size': [4, 4], 'quad': [[0, 0], [4, 0], [4, 4], [0, 4]]}
        art = Image.new('RGBA', (4, 2), (25, 75, 100, 255))
        for marker, expected in ((False, (255, 255, 255, 255)), (True, (0, 0, 0, 0))):
            renderer = self.renderer([slot], workbench=marker)
            out = renderer.render_scene(renderer.data['scenes'][0], art, 'contain', {})
            self.assertEqual(out.getpixel((0, 0)), expected)
            self.assertEqual(out.getpixel((0, 1)), (25, 75, 100, 255))

    def test_optional_trim_removes_artwork_alpha_padding_before_placement(self):
        slot = {'kind': 'slot', 'native_size': [4, 4], 'quad': [[0, 0], [4, 0], [4, 4], [0, 4]]}
        art = Image.new('RGBA', (8, 8))
        art.paste((50, 100, 150, 255), (2, 2, 6, 6))
        image_path = self.folder / 'art.png'
        art.save(image_path)
        renderer = self.renderer([slot], workbench=True)
        settings = {'fit': 'contain', 'width': 0, 'height': 0, 'format': 'png', 'trim': True}
        out = list(renderer.render(image_path, settings))[0][1]
        self.assertEqual(out.getchannel('A').getextrema(), (255, 255))

    def test_static_clipstack_and_legacy_contain_pixels_match_original_v1_to_v4(self):
        spec = importlib.util.spec_from_file_location('original_engine_effects_test', PORTABLE / 'engine.py')
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        base = Image.new('RGBA', (4, 4), (40, 60, 100, 128))
        paint = Image.new('RGBA', (4, 4), (200, 120, 40, 90))
        base.save(self.folder / 'base.png'); paint.save(self.folder / 'paint.png')
        ops = [{'kind': 'clipstack', 'base': 'base.png', 'ops': [{'kind': 'static', 'asset': 'paint.png', 'blend': 'multiply'}]}]
        art = Image.new('RGBA', (2, 4), (10, 20, 30, 200))
        for version in (1, 2, 3, 4):
            renderer = self.renderer(ops, version=version)
            legacy = original.Renderer(renderer.path)
            self.assertEqual(renderer.render_scene(renderer.data['scenes'][0], art, 'contain', {}).tobytes(),
                             legacy.render_scene(legacy.data['scenes'][0], art, 'contain', {}).tobytes())
        self.assertEqual(engine.fit_image(art, (4, 4), 'contain').tobytes(), original.fit_image(art, (4, 4), 'contain').tobytes())

    def test_unsupported_fill_opacity_remains_an_explicit_error(self):
        layer = Layer('shade', mode=BlendMode.SOFT_LIGHT)
        layer.fill_opacity = 128
        with self.assertRaisesRegex(ValueError, '填充不透明度'):
            engine.Compiler(self.folder, 'PRODUCT').compile_layers([layer], (4, 4), 'test')

    def test_fixed_scene_cache_reuses_final_pixels_returns_copies_and_resets_for_size(self):
        fixed_image = Image.new('RGBA', (4, 4), (25, 75, 150, 128))
        fixed_image.save(self.folder / 'fixed.png')
        renderer = self.renderer([{'kind': 'static', 'asset': 'fixed.png'}], workbench=True)
        renderer.data['scenes'][0]['fixed'] = True
        artwork_paths = [self.folder / 'red.png', self.folder / 'green.png']
        for path, color in zip(artwork_paths, ('red', 'green')):
            Image.new('RGB', (4, 4), color).save(path)
        calls = []
        original_render_scene = renderer.render_scene
        def count_scene(*args, **kwargs):
            calls.append(1)
            return original_render_scene(*args, **kwargs)
        renderer.render_scene = count_scene
        settings = {'fit': 'contain', 'width': 0, 'height': 0, 'format': 'png'}
        first = list(renderer.render(artwork_paths[0], settings))[0][1]
        expected = first.tobytes()
        first.putpixel((0, 0), (255, 255, 255, 255))
        second = list(renderer.render(artwork_paths[1], settings))[0][1]
        self.assertEqual(second.tobytes(), expected)
        self.assertEqual(len(calls), 1)
        settings.update(width=16, height=16)
        resized = list(renderer.render(artwork_paths[1], settings))[0][1]
        self.assertEqual(resized.size, (16, 16))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(renderer._fixed_images), 1)


if __name__ == '__main__': unittest.main()
