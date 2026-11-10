"""Independent source-over checks for newly supported Photoshop blend modes."""
import unittest
from PIL import Image
from blend_modes import composite_rgba
from psd_tools.constants import BlendMode
import test_effects as fixtures


def blend(b, s, mode):
    if mode == 'lighten': return max(b, s)
    if mode == 'overlay': return 2*b*s if b <= .5 else 1-2*(1-b)*(1-s)
    if mode == 'hard_light': return 2*b*s if s <= .5 else 1-2*(1-b)*(1-s)
    if mode == 'difference': return abs(b-s)
    if mode == 'exclusion': return b+s-2*b*s
    if mode == 'linear_dodge': return min(1, b+s)
    if mode == 'linear_burn': return max(0, b+s-1)
    if mode == 'color_dodge': return 0 if b == 0 else 1 if s == 1 else min(1, b/(1-s))
    if mode == 'color_burn': return 1 if b == 1 else 0 if s == 0 else 1-min(1, (1-b)/s)
    raise AssertionError(mode)


MODES = ['lighten','overlay','hard_light','difference','exclusion',
         'linear_dodge','linear_burn','color_dodge','color_burn']


class ExtendedBlendTests(unittest.TestCase):
    def test_color_and_transparency_against_scalar_reference(self):
        pairs = [((0,128,255,255),(255,128,0,255)),
                 ((43,180,92,133),(180,53,217,197)),
                 ((92,83,200,0),(31,199,100,72))]
        for mode in MODES:
            for back, src in pairs:
                with self.subTest(mode=mode, back=back):
                    b, s = Image.new('RGBA',(3,4),back),Image.new('RGBA',(3,4),src)
                    out = composite_rgba(b,s,mode,chunk_rows=1)
                    ab, a = back[3]/255,src[3]/255
                    alpha = a+ab*(1-a)
                    rgb = [round(255*((1-a)*ab*(cb/255)+(1-ab)*a*(cs/255)+a*ab*blend(cb/255,cs/255,mode))/alpha)
                           for cb,cs in zip(back[:3],src[:3])]
                    self.assertEqual(out.getpixel((1,3)),tuple(rgb+[round(alpha*255)]))
                    self.assertEqual(b.getpixel((0,0)),back)
                    self.assertEqual(s.getpixel((0,0)),src)

    def test_transparent_source_and_empty_backdrop(self):
        for mode in MODES:
            back=Image.new('RGBA',(2,2),(13,81,123,177))
            empty=Image.new('RGBA',(2,2),(230,20,77,0))
            self.assertEqual(composite_rgba(back,empty,mode).tobytes(),back.tobytes())
            self.assertEqual(composite_rgba(empty,back,mode).tobytes(),Image.alpha_composite(empty,back).tobytes())


class ExtendedCompilerTests(unittest.TestCase):
    setUp = fixtures.EffectsTests.setUp
    tearDown = fixtures.EffectsTests.tearDown
    def test_extended_modes_are_not_flattened_as_normal(self):
        import engine
        for mode in MODES:
            with self.subTest(mode=mode):
                compiler=engine.Compiler(self.folder,'PRODUCT')
                ops=compiler.compile_layers([fixtures.Layer('base'),fixtures.Layer('effect',mode=getattr(BlendMode,mode.upper()))],(4,4),'scene')
                self.assertEqual(ops[-1]['blend'],mode)
