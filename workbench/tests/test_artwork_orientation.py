import json
from pathlib import Path
import tempfile
import unittest
import math
from unittest.mock import patch

from PIL import Image
import numpy as np
import engine


def landscape():
    image=Image.new('RGBA',(160,80),'red');image.paste('blue',(80,0,160,80));return image


class ArtworkOrientationTests(unittest.TestCase):
    def test_print_brightness_is_shared_by_scenes_and_preserves_static_pixels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = Image.new('RGBA', (16, 16), (115, 120, 125, 255))
            original.paste((255, 255, 255, 255), (12, 0, 16, 16))
            original.save(root/'art.png')
            Image.new('RGBA', (48, 24), (226, 213, 201, 255)).save(root/'wall.png')
            scenes = []
            for number, x in enumerate((4, 24), 1):
                quad = [[x, 4], [x+16, 4], [x+16, 20], [x, 20]]
                if number == 2:
                    quad = [quad[1], quad[0], quad[3], quad[2]]
                scenes.append(dict(name=str(number), width=48, height=24, ops=[
                    dict(kind='static', asset='wall.png'),
                    dict(kind='slot', native_size=[16, 16], quad=quad, opacity=255)]))
            manifest = root/'template.json'
            manifest.write_text(json.dumps(dict(version=4, workbench=True, scenes=scenes)))
            renderer = engine.Renderer(manifest)
            settings = dict(fit='contain', width=0, height=0, format='png')
            before = dict(renderer.render(root/'art.png', settings))
            neutral = dict(renderer.render(root/'art.png', dict(settings, brightness=0)))
            brighter = dict(renderer.render(root/'art.png', dict(settings, brightness=15)))
            darker = dict(renderer.render(root/'art.png', dict(settings, brightness=-15)))
            for name, middle, white in [('1', (9, 10), (18, 10)), ('2', (34, 10), (25, 10))]:
                self.assertEqual(before[name].tobytes(), neutral[name].tobytes())
                self.assertGreater(brighter[name].getpixel(middle)[0], before[name].getpixel(middle)[0])
                self.assertLess(darker[name].getpixel(middle)[0], before[name].getpixel(middle)[0])
                self.assertEqual(brighter[name].getpixel(white), before[name].getpixel(white))
                self.assertEqual(brighter[name].getpixel((0, 0)), before[name].getpixel((0, 0)))
            with Image.open(root/'art.png') as saved:
                self.assertEqual(saved.tobytes(), original.tobytes())

    def test_auto_turns_landscape_upright_before_fitting_without_losing_sides(self):
        result=engine.fit_image(landscape(),(80,160),'cover',orientation='auto')
        self.assertEqual(result.getpixel((40,20)),(255,0,0,255))
        self.assertEqual(result.getpixel((40,140)),(0,0,255,255))
        self.assertEqual(result.getchannel('A').getextrema(),(255,255))

    def test_keep_and_clockwise_and_counterclockwise_are_distinct(self):
        art=landscape()
        keep=engine.fit_image(art,(80,160),'contain',preserve_alpha=True,orientation='original')
        clockwise=engine.fit_image(art,(80,160),'contain',orientation='cw90')
        counter=engine.fit_image(art,(80,160),'contain',orientation='ccw90')
        self.assertEqual(keep.getpixel((40,0))[3],0)
        self.assertEqual(clockwise.getpixel((40,20)),(255,0,0,255))
        self.assertEqual(counter.getpixel((40,20)),(0,0,255,255))
        self.assertEqual(art.size,(160,80))

    def test_matching_and_near_square_directions_stay_unchanged(self):
        self.assertEqual(engine.orientation_info(landscape(),(160,80),'auto')['rotation'],0)
        self.assertEqual(engine.orientation_info(landscape(),(100,100),'auto')['rotation'],0)
        self.assertEqual(engine.orientation_info(Image.new('RGBA',(105,100),'red'),(80,160),'auto')['rotation'],0)

    def test_transparent_padding_does_not_confuse_direction_detection(self):
        padded=Image.new('RGBA',(200,300));padded.paste(landscape(),(20,110))
        self.assertEqual(engine.orientation_info(padded,(80,160),'auto')['rotation'],90)
        output=engine.fit_image(padded,(80,160),'auto',orientation='auto')
        self.assertEqual(output.getpixel((40,20)),(255,0,0,255))

    def test_v7_uses_trimmed_artwork_direction_before_covering(self):
        art=Image.new('RGBA',(200,300),'white');art.paste(landscape(),(20,110))
        output=engine.fit_image(art,(80,160),'cover_bleed',orientation='auto')
        self.assertEqual(output.getpixel((40,20)),(255,0,0,255))
        self.assertEqual(output.getpixel((40,140)),(0,0,255,255))

    def test_renderer_handles_multiple_print_directions_and_legacy_batch_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);landscape().save(root/'art.png')
            def scene(name,size):
                w,h=size
                return dict(name=name,width=w,height=h,ops=[dict(kind='slot',native_size=[w,h],quad=[[0,0],[w,0],[w,h],[0,h]],opacity=255)])
            manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[scene('portrait',(80,160)),scene('landscape',(160,80)),scene('portrait2',(80,160))])))
            renderer=engine.Renderer(manifest);settings=dict(fit='contain',fit_policy=2,width=0,height=0,format='png')
            legacy=list(renderer.render(root/'art.png',settings))
            self.assertEqual(legacy[0][1].getpixel((40,0))[3],0)
            modern=list(renderer.render(root/'art.png',dict(settings,orientation='auto')))
            self.assertEqual(modern[0][1].getpixel((40,20)),(255,0,0,255))
            self.assertEqual(modern[1][1].getpixel((20,40)),(255,0,0,255))
            self.assertEqual(modern[2][1].tobytes(),modern[0][1].tobytes())

    def test_invalid_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.settings_checked(dict(width=0,height=0,ppi=72,quality=100,fit='auto',format='jpg',orientation='unknown'))

    def test_arbitrary_angle_cover_fills_and_contain_preserves_the_whole_artwork(self):
        art=landscape()
        filled=engine.fit_image(art,(80,160),'cover',rotation=45)
        contained=engine.fit_image(art,(80,160),'contain',rotation=45,preserve_alpha=True)
        self.assertEqual(filled.getchannel('A').getextrema(),(255,255))
        self.assertEqual(contained.getpixel((0,0))[3],0)
        self.assertTrue(any(pixel[:3]==(255,0,0) and pixel[3]==255 for pixel in contained.getdata()))
        self.assertTrue(any(pixel[:3]==(0,0,255) and pixel[3]==255 for pixel in contained.getdata()))

    def test_rotation_happens_inside_a_fixed_45_degree_mockup(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);landscape().save(root/'art.png')
            angle=math.pi/4
            quad=[[120+x*math.cos(angle)-y*math.sin(angle),120+x*math.sin(angle)+y*math.cos(angle)] for x,y in [(-40,-80),(40,-80),(40,80),(-40,80)]]
            manifest=root/'template.json';manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name='tilted',width=240,height=240,ops=[dict(kind='slot',native_size=[80,160],quad=quad,opacity=255)])])))
            renderer=engine.Renderer(manifest);settings=dict(fit='cover',fit_policy=2,orientation='original',width=0,height=0,format='png')
            images=[list(renderer.render(root/'art.png',dict(settings,rotation=rotation)))[0][1] for rotation in (0,30,45,90,-35)]
            self.assertTrue(all(image.size==(240,240) for image in images))
            self.assertTrue(all(image.getchannel('A').tobytes()==images[0].getchannel('A').tobytes() for image in images))
            self.assertTrue(images[1].tobytes()!=images[0].tobytes())

    def test_rotation_can_offset_auto_direction_and_rejects_invalid_values(self):
        self.assertEqual(engine.orientation_info(landscape(),(80,160),'auto',rotation=-90)['rotation'],0)
        self.assertEqual(engine.orientation_info(landscape(),(80,160),'original',rotation=-45)['rotation'],315)
        for value in (float('nan'),float('inf'),181,True,'45'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                engine.settings_checked(dict(width=0,height=0,ppi=72,quality=100,fit='auto',format='jpg',rotation=value))

    def test_small_manual_angle_retains_antialiasing_when_downsampling_large_art(self):
        checker=((np.indices((2048,2048)).sum(axis=0)%2)*255).astype('uint8')
        art=Image.fromarray(checker).convert('RGBA')
        for angle in (.1,10,45):
            with self.subTest(angle=angle):
                output=engine.fit_image(art,(128,128),'cover',rotation=angle)
                self.assertLess(np.asarray(output)[:,:,0].std(),2)
                self.assertEqual(output.getchannel('A').getextrema(),(255,255))

    def test_rotated_stretch_filters_the_axis_that_shrinks_more(self):
        stripe=(np.sin(np.arange(2048)*2*np.pi/8)*127+128).astype('uint8')
        art=Image.fromarray(np.tile(stripe,(2048,1))).convert('RGBA')
        for angle in (.1,10):
            with self.subTest(angle=angle):
                output=engine.fit_image(art,(128,1024),'stretch',rotation=angle)
                pixels=np.asarray(output)
                self.assertLess(pixels[:,:,0][pixels[:,:,3]==255].std(),3)
