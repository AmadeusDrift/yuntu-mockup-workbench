"""Adjustment integration retains live artwork and bakes fixed backgrounds once."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from psd_tools.constants import ColorMode
import engine
import catalog
import test_effects as fixtures


class AdjustmentPipelineTests(unittest.TestCase):
    setUp = fixtures.EffectsTests.setUp
    tearDown = fixtures.EffectsTests.tearDown
    renderer = fixtures.EffectsTests.renderer

    def adjustment(self, clipping=False):
        layer=fixtures.Layer('反相',kind='invert',clipping=clipping)
        layer._psd=SimpleNamespace(depth=8,color_mode=ColorMode.RGB)
        return layer

    def test_dynamic_clipped_adjustment_follows_replaced_artwork_and_alpha(self):
        compiler=engine.Compiler(self.folder,'PRODUCT')
        ops=compiler.compile_layers([fixtures.Layer('PRODUCT',kind='smartobject'),self.adjustment(True)],(4,4),'1')
        renderer=self.renderer(ops,workbench=True)
        baseline=self.renderer([ops[0]['base_op']],workbench=True)
        for color in ((30,100,200,128),(210,15,80,255)):
            art=Image.new('RGBA',(4,4),color)
            out=renderer.render_scene(renderer.data['scenes'][0],art,'stretch',{})
            base=baseline.render_scene(baseline.data['scenes'][0],art,'stretch',{}).getpixel((2,2))
            self.assertEqual(out.getpixel((2,2)),tuple(255-v for v in base[:3])+(base[3],))

    def test_static_prefix_baked_once_but_dynamic_adjustment_remains_live(self):
        compiler=engine.Compiler(self.folder,'PRODUCT')
        back=fixtures.Layer('background');back.topil=lambda:Image.new('RGBA',(4,4),(10,20,30,255))
        ops=compiler.compile_layers([back,self.adjustment(),fixtures.Layer('PRODUCT',kind='smartobject'),self.adjustment(True)],(4,4),'1')
        original=self.renderer(ops,workbench=True)
        baked=catalog.freeze_static_prefixes(compiler,ops,(4,4))
        self.assertEqual([op['kind'] for op in baked],['static','clipstack'])
        optimized=self.renderer(baked,workbench=True)
        for color in ((1,2,3,127),(80,100,245,255)):
            art=Image.new('RGBA',(4,4),color)
            self.assertEqual(original.render_scene(original.data['scenes'][0],art,'stretch',{}).tobytes(),
                             optimized.render_scene(optimized.data['scenes'][0],art,'stretch',{}).tobytes())

    def test_adjustment_with_unsupported_blending_is_explicitly_blocked(self):
        from psd_tools.constants import BlendMode
        layer=self.adjustment();layer.blend_mode=BlendMode.MULTIPLY
        with self.assertRaisesRegex(ValueError,'普通模式'):
            engine.Compiler(self.folder,'PRODUCT').compile_layers([layer],(4,4),'1')
