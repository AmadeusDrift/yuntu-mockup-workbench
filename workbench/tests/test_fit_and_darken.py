import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
from psd_tools.constants import BlendMode
import engine
from blend_modes import composite_rgba
from test_effects import Layer


class FitAndDarkenTests(unittest.TestCase):
    def test_horizontal_full_artwork_covers_portrait_area_without_stretching(self):
        art=Image.new('RGBA',(240,80),'red')
        ImageDraw.Draw(art).rectangle((80,0,159,79),fill='blue')
        result=engine.fit_image(art,(80,160),'auto')
        self.assertEqual(result.getchannel('A').getextrema(),(255,255))
        self.assertEqual(result.getpixel((40,0)),(0,0,255,255))
        self.assertEqual(engine.auto_fit_info(art,(80,160))['mode'],'cover')
        # Old batch settings retain their earlier placement, rather than mixing outputs on resume.
        self.assertEqual(engine.fit_image(art,(80,160),'auto',policy=1).getpixel((0,0))[3],0)

    def test_rectangular_artwork_with_transparent_padding_is_full_artwork(self):
        art=Image.new('RGBA',(260,100));art.paste((200,30,60,255),(10,10,250,90))
        result=engine.fit_image(art,(80,160),'auto')
        self.assertEqual(result.getchannel('A').getextrema(),(255,255))
        self.assertEqual(engine.auto_fit_info(art,(80,160))['mode'],'cover')

    def test_cutout_artwork_remains_complete_with_transparency(self):
        art=Image.new('RGBA',(240,160));ImageDraw.Draw(art).ellipse((10,10,230,150),fill='red')
        info=engine.auto_fit_info(art,(80,160))
        self.assertEqual(info['mode'],'contain')
        result=engine.fit_image(art,(80,160),'auto')
        self.assertEqual(result.getpixel((40,0))[3],0)

    def test_v7_white_padding_and_alpha_padding_are_removed_before_cover(self):
        art=Image.new('RGBA',(100,200),'white');art.paste((180,20,60,255),(12,45,88,170))
        padded=Image.new('RGBA',(120,220));padded.paste(art,(10,10))
        result=engine.fit_image(padded,(100,160),'cover_bleed')
        self.assertEqual(result.getchannel('A').getextrema(),(255,255))
        self.assertLess(max(result.getpixel((50,0))[:3]),220)
        # The ordinary cover option still preserves deliberately designed white margins.
        self.assertEqual(engine.fit_image(art,(100,160),'cover').getpixel((50,0)),(255,255,255,255))

    def test_v7_does_not_trim_colored_background_or_fail_on_solid_white(self):
        art=Image.new('RGBA',(100,200),'#e6f0ff');art.paste('red',(30,60,70,140))
        result=engine.fit_image(art,(100,160),'cover_bleed')
        self.assertEqual(result.getpixel((0,0)),(230,240,255,255))
        self.assertEqual(engine.fit_image(Image.new('RGBA',(100,200),'white'),(100,160),'cover_bleed').getpixel((0,0)),(255,255,255,255))

    def test_v7_panorama_does_not_allocate_an_enormous_resized_intermediate(self):
        art=Image.new('RGBA',(10000,10),'red')
        original=Image.Image.resize;sizes=[]
        def resize(image,size,*args,**kwargs):
            sizes.append(size)
            return original(image,size,*args,**kwargs)
        with patch.object(Image.Image,'resize',resize):
            result=engine.fit_image(art,(100,160),'cover_bleed')
        self.assertEqual(result.size,(100,160))
        self.assertTrue(all(w*h<=100*160 for w,h in sizes))

    def test_cover_bleed_fills_top_of_arch_but_keeps_mask_outline(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);mask=Image.new('L',(100,160));d=ImageDraw.Draw(mask)
            d.ellipse((10,10,90,90),fill=255);d.rectangle((10,50,90,150),fill=255);mask.save(root/'mask.png')
            art=Image.new('RGB',(100,200),'white');art.paste('#b52e43',(5,55,95,195));art.save(root/'art.png')
            slot=dict(kind='slot',native_size=[100,160],quad=[[0,0],[100,0],[100,160],[0,160]],mask='mask.png',opacity=255)
            manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name='1',width=100,height=160,ops=[slot])])) )
            renderer=engine.Renderer(manifest)
            settings=dict(fit='cover_bleed',fit_policy=2,width=0,height=0,format='png')
            result=list(renderer.render(root/'art.png',settings))[0][1]
            self.assertEqual(result.getpixel((0,0))[3],0)
            self.assertEqual(result.getpixel((50,15))[:3],(181,46,67))
            self.assertEqual(result.getchannel('A').tobytes(),mask.tobytes())

    def test_compiler_accepts_darken_and_preserves_operation_mode(self):
        with tempfile.TemporaryDirectory() as td:
            compiler=engine.Compiler(Path(td),'PRODUCT')
            ops=compiler.compile_layers([Layer('background'),Layer('固定保护 1',mode=BlendMode.DARKEN)],(4,4),'1')
            self.assertEqual(ops[-1]['blend'],'darken')

    def test_v7_preprocessing_is_reused_across_different_sized_slots(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);art=Image.new('RGB',(100,200),'white');art.paste('red',(10,50,90,180));art.save(root/'art.png')
            scenes=[dict(name=str(i),width=100,height=160,ops=[dict(kind='slot',native_size=size,quad=[[0,0],[100,0],[100,160],[0,160]],opacity=255)]) for i,size in enumerate(([100,160],[50,80],[100,160]))]
            manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=scenes)))
            renderer=engine.Renderer(manifest)
            with patch.object(engine,'prepare_cover_artwork',wraps=engine.prepare_cover_artwork) as prepare:
                images=list(renderer.render(root/'art.png',dict(fit='cover_bleed',fit_policy=2,width=0,height=0,format='png')))
                self.assertEqual(prepare.call_count,1)
            self.assertEqual(len(images),3)

    def test_new_and_saved_legacy_settings_use_their_own_placement_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);Image.new('RGB',(240,80),'red').save(root/'art.png')
            manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name='1',width=80,height=160,ops=[dict(kind='slot',native_size=[80,160],quad=[[0,0],[80,0],[80,160],[0,160]],opacity=255)])])))
            renderer=engine.Renderer(manifest);settings=dict(fit='auto',width=0,height=0,format='png')
            legacy=list(renderer.render(root/'art.png',settings))[0][1]
            modern=list(renderer.render(root/'art.png',dict(settings,fit_policy=2)))[0][1]
            self.assertEqual(legacy.getpixel((40,0))[3],0)
            self.assertEqual(modern.getpixel((40,0))[3],255)

    def test_darken_uses_component_minimum_and_correct_alpha(self):
        pairs=[((100,200,50,255),(150,40,80,255)),((90,180,220,140),(230,35,110,170)),
               ((40,170,60,0),(170,20,240,120)),((200,100,30,70),(20,200,150,0))]
        for b,s in pairs:
            base,paint=Image.new('RGBA',(5,7),b),Image.new('RGBA',(5,7),s)
            output=composite_rgba(base,paint,'darken')
            ab,a=b[3]/255,s[3]/255;alpha=a+ab*(1-a)
            rgb=[round(255*((1-a)*ab*(cb/255)+(1-ab)*a*(cs/255)+a*ab*min(cb,cs)/255)/alpha) if alpha else 0 for cb,cs in zip(b[:3],s[:3])]
            self.assertTrue(np.all(np.abs(np.array(output.getpixel((2,3)))-np.array(rgb+[round(alpha*255)]))<=1))
            self.assertEqual(base.getpixel((0,0)),b);self.assertEqual(paint.getpixel((0,0)),s)
