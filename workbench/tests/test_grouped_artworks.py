import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import engine
from service import Service


class GroupedArtworksTests(unittest.TestCase):
    def test_fixed_only_template_does_not_require_replacement_mapping(self):
        from artwork_sources import validate_roles
        validate_roles({'scenes':[{'fixed':True,'ops':[]}]})

    def test_single_file_called_left_is_still_an_ordinary_artwork(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'001';folder.mkdir();file=folder/'left.png'
            Image.new('RGB',(20,20),'red').save(file)
            self.assertEqual(Service.input_images(root),[file])

    def group(self, root, name):
        folder=root/name;folder.mkdir(parents=True)
        for role,color in [('左','red'),('中','green'),('右','blue')]:
            Image.new('RGB',(20,20),color).save(folder/(role+'.png'))
        Image.new('RGB',(60,20),'yellow').save(folder/'三联预览_90x40比例.png')
        return folder

    def manifest(self,root):
        ops=[dict(kind='slot',artwork_role=role,native_size=[20,20],quad=[[i*20,0],[(i+1)*20,0],[(i+1)*20,20],[i*20,20]],opacity=255) for i,role in enumerate(['left','center','right'])]
        p=root/'template.json';p.write_text(json.dumps(dict(version=4,workbench=True,scenes=[dict(name='1',width=60,height=20,slots=3,ops=ops)])))
        return p

    def test_group_scan_and_thumbnail_ignore_composite_preview(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'input';source.mkdir()
            g10=self.group(source,'010');g2=self.group(source,'002')
            self.assertEqual(Service.input_images(source),[g/ (r+'.png') for g in (g2,g10) for r in ('左','中','右')])
            listing=Service(root/'data').artworks(source)
            self.assertEqual(listing['total'],6)
            self.assertEqual(listing['files'][0]['kind'],'image')
            self.assertEqual(listing['files'][0]['name'],'002/左.png')

    def test_incomplete_group_is_not_silently_processed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);group=self.group(root,'001');(group/'右.png').unlink()
            with self.assertRaisesRegex(ValueError,'右'):Service.input_images(root)

    def test_seven_scenes_export_three_independent_sets_per_numbered_product(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'input';src.mkdir();out=root/'output';out.mkdir()
            self.group(src,'002');self.group(src,'010');manifest=self.manifest(root)
            data=json.loads(manifest.read_text())
            scene=data['scenes'][0]
            for op in scene['ops']:op.pop('artwork_role',None)
            data['scenes']=[dict(scene,name=str(i)) for i in range(1,8)]
            manifest.write_text(json.dumps(data))
            settings=dict(width=0,height=0,ppi=72,quality=95,fit='stretch',format='png',naming=dict(folder='{index:03}',image='{scene:02}',start=5))
            dest,state=engine.batch(manifest,src,out,settings)
            for number in ('005','006'):
                for role,color in [('左',(255,0,0)),('中',(0,128,0)),('右',(0,0,255))]:
                    files=sorted((dest/number/role).glob('*.png'))
                    self.assertEqual([p.name for p in files],[f'{i:02}.png' for i in range(1,8)])
                    for file in files:
                        with Image.open(file) as image:self.assertEqual(image.getpixel((10,10))[:3],color)
            self.assertEqual(len(list(dest.rglob('*.png'))),42)

    def test_three_same_size_slots_use_different_artworks_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'input';src.mkdir();out=root/'output';out.mkdir()
            self.group(src,'002');self.group(src,'010');manifest=self.manifest(root)
            settings=engine.settings_checked(dict(width=0,height=0,ppi=72,quality=95,fit='stretch',format='png',naming=dict(folder='{name}',image='{scene:02}',start=1)))
            dest,state=engine.batch(manifest,src,out,settings)
            self.assertEqual(set(state['records']),{g+'/'+r+'.png' for g in ('002','010') for r in ('左','中','右')})
            for role,color in [('左',(255,0,0)),('中',(0,128,0)),('右',(0,0,255))]:
                with Image.open(dest/f'002/{role}/01.png') as result:
                    self.assertEqual([result.getpixel((x,10))[:3] for x in (10,30,50)],[color]*3)
            before=(dest/'002/左/01.png').stat().st_mtime_ns
            engine.batch(manifest,src,out,settings,resume=dest)
            self.assertEqual((dest/'002/左/01.png').stat().st_mtime_ns,before)
            Image.new('RGB',(21,20),'white').save(src/'002/中.png')
            with self.assertRaisesRegex(ValueError,'不能继续'):engine.batch(manifest,src,out,settings,resume=dest)

    def test_group_requires_explicit_template_roles(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);group=self.group(root,'001');manifest=self.manifest(root)
            data=json.loads(manifest.read_text());del data['scenes'][0]['ops'][0]['artwork_role'];manifest.write_text(json.dumps(data))
            settings=dict(width=0,height=0,ppi=72,quality=95,fit='stretch',format='png')
            with self.assertRaisesRegex(ValueError,'对应'):list(engine.Renderer(manifest).render(group,settings))

    def test_all_fit_modes_keep_three_sources_separate(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);group=self.group(root,'001');manifest=self.manifest(root)
            for mode in ('auto','contain','cover','cover_bleed','stretch'):
                with self.subTest(mode=mode):
                    settings=dict(width=0,height=0,ppi=72,quality=95,fit=mode,format='png',orientation='original',rotation=0)
                    image=list(engine.Renderer(manifest).render(group,settings))[0][1]
                    self.assertEqual([image.getpixel((x,10))[:3] for x in (10,30,50)],[(255,0,0),(0,128,0),(0,0,255)])
                    image.close()

    def test_nested_same_names_use_relative_identity_and_numbered_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'input';src.mkdir();out=root/'output';out.mkdir()
            for number,color in [('10','red'),('2','blue')]:
                folder=src/number;folder.mkdir();Image.new('RGB',(20,20),color).save(folder/'same.png')
            manifest=self.manifest(root)
            settings=dict(width=0,height=0,ppi=72,quality=95,fit='stretch',format='png',naming=dict(folder='{index:03}',image='{scene:02}',start=1))
            dest,state=engine.batch(manifest,src,out,settings)
            self.assertEqual([row[0] for row in state['identity']['files']],['2/same.png','10/same.png'])
            self.assertEqual(set(state['records']),{'2/same.png','10/same.png'})
            self.assertTrue((dest/'001/01.png').exists());self.assertTrue((dest/'002/01.png').exists())

    def test_role_mapping_persists_and_invalidates_cache(self):
        import catalog
        from cache import cache_key
        from copy import deepcopy
        template=dict(digest='test',scenes=[dict(id='s',name='1',mode='replace',targets=['x'],candidates=[dict(id='x')])])
        updated=deepcopy(template);updated['scenes']=catalog.updated_scenes(template,[dict(id='s',mode='replace',targets=['x'],artwork_roles={'x':'left'})])
        self.assertEqual(updated['scenes'][0]['artwork_roles'],{'x':'left'})
        self.assertNotEqual(cache_key(template),cache_key(updated))
        with self.assertRaises(ValueError):catalog.updated_scenes(template,[dict(id='s',mode='replace',targets=['x'],artwork_roles={'x':'invalid'})])
