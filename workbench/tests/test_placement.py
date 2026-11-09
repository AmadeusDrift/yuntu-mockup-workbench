import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import engine


class PlacementTests(unittest.TestCase):
    def renderer(self, size=(40, 40), mask=False):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        w,h=size
        slot=dict(kind='slot', native_size=list(size),quad=[[0,0],[w,0],[w,h],[0,h]])
        if mask:
            m=Image.new('L',size,0);m.paste(255,(10,10,30,30));m.save(root/'mask.png');slot['mask']='mask.png'
        scene=dict(name='test',width=w,height=h,ops=[slot])
        engine.write_json(root/'template.json',dict(version=4,workbench=True,scenes=[scene]))
        renderer=engine.Renderer(root/'template.json')
        return renderer,renderer.data['scenes'][0]

    def test_cover_drag_reveals_original_outside_center_crop(self):
        renderer,scene=self.renderer()
        art=Image.new('RGBA',(120,40),'blue');art.paste('red',(0,0,40,40));art.paste('green',(40,0,80,40))
        result=renderer.render_scene(scene,art,'cover',{},placement={'offset_x':100})
        self.assertEqual(result.getpixel((20,20)),(255,0,0,255))

    def test_scale_center_and_mask_remain_in_native_plane(self):
        renderer,scene=self.renderer(mask=True)
        result=renderer.render_scene(scene,Image.new('RGBA',(40,40),'red'),'contain',{},placement={'scale':50,'offset_x':25})
        self.assertEqual(result.getchannel('A').getbbox(),(20,10,30,30))

    def test_default_is_pixel_identical_for_all_fits_and_rotations(self):
        renderer,scene=self.renderer()
        art=Image.new('RGBA',(63,27));art.paste((80,100,160,255),(3,4,60,23))
        for mode in engine.MODES:
            for angle in (0,90,27):
                for policy in (1,2):
                    with self.subTest(mode=mode,angle=angle,policy=policy):
                        before=renderer.render_scene(scene,art,mode,{},rotation=angle,fit_policy=policy)
                        after=renderer.render_scene(scene,art,mode,{},rotation=angle,fit_policy=policy,placement={'scale':100,'offset_x':0,'offset_y':0})
                        self.assertEqual(before.tobytes(),after.tobytes())

    def test_full_artwork_layout_includes_cover_overflow(self):
        art,layout=engine.prepare_placement_artwork(Image.new('RGBA',(120,40),'red'),(40,40),'cover')
        self.assertEqual(art.size,(120,40))
        self.assertEqual((layout['x'],layout['y'],layout['width'],layout['height']),(-40,0,120,40))
        self.assertEqual(layout['affine'],[1,0,0,1,-40,0])

    def test_settings_validate_numeric_finite_ranges(self):
        base=dict(width=0,height=0,ppi=72,quality=90,fit='cover',format='png')
        for key,values in {'scale':[True,0,301,float('nan'),'100'], 'offset_x':[False,-101,float('inf'),'0'], 'offset_y':[101,None]}.items():
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    engine.settings_checked(dict(base,**{key:value}))
        for key,value in [('scale',10),('scale',300),('offset_x',-100),('offset_y',100)]:
            self.assertEqual(engine.settings_checked(dict(base,**{key:value}))[key],value)

    def test_render_settings_apply_placement(self):
        renderer,scene=self.renderer()
        settings=dict(fit='contain',width=0,height=0,format='png',scale=50,offset_y=-25)
        _,result=next(renderer._render_images(Image.new('RGBA',(40,40),'red'),settings,{}))
        self.assertEqual(result.getchannel('A').getbbox(),(10,0,30,20))

    def test_offsets_are_relative_to_each_native_stage(self):
        for size in ((40,40),(80,40),(40,80)):
            renderer,scene=self.renderer(size)
            result=renderer.render_scene(scene,Image.new('RGBA',size,'red'),'stretch',{},placement={'scale':50,'offset_x':25})
            w,h=size
            self.assertEqual(result.getchannel('A').getbbox(),(w//2,h//4,w,3*h//4))

    def test_placement_cache_does_not_reuse_previous_position(self):
        renderer,scene=self.renderer()
        art=Image.new('RGBA',(40,40),'red');cache={}
        first=renderer.render_scene(scene,art,'stretch',cache,placement={'scale':50,'offset_x':-25})
        second=renderer.render_scene(scene,art,'stretch',cache,placement={'scale':50,'offset_x':25})
        self.assertEqual(first.getchannel('A').getbbox(),(0,10,20,30))
        self.assertEqual(second.getchannel('A').getbbox(),(20,10,40,30))

    def test_brightness_and_trim_are_retained_with_placement(self):
        renderer,scene=self.renderer()
        source=renderer.path.parent/'art.png'
        art=Image.new('RGBA',(60,60));art.paste((100,100,100,255),(10,10,50,50));art.save(source)
        settings=dict(fit='contain',trim=True,brightness=20,width=0,height=0,format='png',scale=50,offset_x=25)
        _,result=next(renderer.render(source,settings))
        self.assertEqual(result.getchannel('A').getbbox(),(20,10,40,30))
        self.assertGreater(result.getpixel((25,20))[0],100)

    def test_rotated_stretch_layout_maps_source_center_to_stage_center(self):
        art,layout=engine.prepare_placement_artwork(Image.new('RGBA',(120,40),'red'),(40,80),'stretch',rotation=33)
        a,b,c,d,x,y=layout['affine']
        self.assertAlmostEqual(a*60+c*20+x,20)
        self.assertAlmostEqual(b*60+d*20+y,40)
        self.assertAlmostEqual(layout['width'],40)
        self.assertAlmostEqual(layout['height'],80)
