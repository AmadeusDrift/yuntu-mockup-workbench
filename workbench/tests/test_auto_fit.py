import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import engine
from service import Service


def settings(**changes):
    return dict(dict(width=0, height=0, ppi=72, quality=100, fit='auto', format='png'), **changes)


class AutoFitTests(unittest.TestCase):
    def test_transparent_artwork_trims_and_contains_preserving_alpha(self):
        art = Image.new('RGBA', (100, 100))
        art.paste((255, 0, 0, 128), (30, 20, 70, 80))
        result = engine.fit_image(art, (100, 100), 'auto')
        self.assertEqual(result.getbbox(), (16, 0, 83, 100))
        self.assertEqual(result.getpixel((50, 50)), (255, 0, 0, 128))
        info = engine.auto_fit_info(art, (100, 100))
        self.assertEqual(info['mode'], 'contain')
        self.assertTrue(info['has_alpha'])

    def test_full_artwork_covers_and_legacy_policy_retains_prior_placement(self):
        art = Image.new('RGBA', (100, 100), 'red')
        near = engine.auto_fit_info(art, (100, 95))
        wide = engine.auto_fit_info(art, (200, 100))
        self.assertEqual(near['mode'], 'cover')
        self.assertAlmostEqual(near['retained_fraction'], .95)
        self.assertEqual(wide['mode'], 'cover')
        self.assertEqual(engine.fit_image(art, (200, 100), 'auto').getbbox(), (0, 0, 200, 100))
        self.assertEqual(engine.fit_image(art, (200, 100), 'auto', policy=1).getbbox(), (50, 0, 150, 100))

    def test_entirely_transparent_artwork_reports_clear_error(self):
        with self.assertRaisesRegex(ValueError, '完全透明'):
            engine.fit_image(Image.new('RGBA', (20, 20)), (20, 20), 'auto')

    def test_auto_load_keeps_faint_border_transparency_and_legacy_normalization(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'art.png'
            art=Image.new('RGBA',(20,20),(255,0,0,240))
            art.paste((255,0,0,255),(1,1,19,19));art.save(path)
            legacy=engine.load_image(path)
            self.assertEqual(legacy.getpixel((0,0))[3],255)
            auto=engine.load_image(path,preserve_alpha=True)
            self.assertEqual(auto.getpixel((0,0))[3],240)

    def test_renderer_decides_per_native_slot_and_reuses_prepared_size(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            Image.new('RGB', (100, 100), 'red').save(root / 'art.png')
            def slot(size):
                return dict(kind='slot', native_size=size, quad=[[0,0],[200,0],[200,100],[0,100]], opacity=255)
            scenes = [dict(name=str(i), width=200, height=100, ops=[slot(size)])
                      for i, size in enumerate(([200,100], [100,95], [200,100]))]
            manifest = root / 'template.json'
            manifest.write_text(json.dumps(dict(version=4, workbench=True, scenes=scenes)))
            renderer = engine.Renderer(manifest)
            with patch.object(engine, 'fit_image', wraps=engine.fit_image) as fit:
                rendered = list(renderer.render(root / 'art.png', settings()))
            self.assertEqual([tuple(c.args[1]) for c in fit.call_args_list], [(200,100),(100,95)])
            self.assertEqual(rendered[0][1].getpixel((0, 50))[3], 0)
            self.assertGreater(rendered[1][1].getpixel((10, 50))[3], 0)

    def test_auto_is_a_valid_setting_without_changing_explicit_legacy_contain(self):
        self.assertEqual(engine.settings_checked(settings())['fit'], 'auto')
        art = Image.new('RGBA', (10, 20), 'red')
        self.assertEqual(engine.fit_image(art, (20, 20), 'contain').getpixel((0, 0)), (255,255,255,255))


class BrowserAndPreferencesTests(unittest.TestCase):
    def test_only_legacy_exact_defaults_migrate_once(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            old=dict(input='',output='',settings=settings(width=1254,height=1254,fit='contain'),limit=0)
            (root/'preferences.json').write_text(json.dumps(old))
            app=Service(root)
            self.assertEqual(app.preferences['settings']['fit'],'auto')
            app.save_preferences(dict(settings=settings(width=1254,height=1254,fit='contain')))
            self.assertEqual(Service(root).preferences['settings']['fit'],'contain')
            old['settings']['width']=800
            (root/'preferences.json').write_text(json.dumps(old))
            self.assertEqual(Service(root).preferences['settings']['width'],800)

    def test_preview_explains_unique_nested_native_sizes_and_batch_defers_output_until_checked(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); source=root/'source';source.mkdir();app=Service(root/'data')
            Image.new('RGB',(100,100),'red').save(source/'art.png')
            app.templates=[dict(id='a',name='Template',scenes=[dict(id='s',mode='fixed',targets=[],candidates=[])])]
            folder=app.cache.root/('a'*64);folder.mkdir()
            slot=dict(kind='slot',native_size=[100,95],quad=[[0,0],[100,0],[100,95],[0,95]],opacity=255)
            manifest=folder/'template.json'
            manifest.write_text(json.dumps(dict(version=4,workbench=True,scenes=[
                dict(name='1',width=100,height=95,ops=[dict(kind='group',ops=[slot],opacity=255)])])))
            app.manifest=lambda template:manifest
            app.preview('a',str(source/'art.png'),settings())
            for _ in range(200):
                if app.task['status'] not in ('running','paused','stopping'):break
                time.sleep(.01)
            self.assertEqual(app.task['status'],'done',app.task['error'])
            self.assertEqual(app.task['preview_fit'][0]['size'],[100,95])
            self.assertEqual(app.task['preview_fit'][0]['mode'],'cover')
            output=root/'new'/'output'
            with patch.object(app,'start',return_value={'ok':True}):
                app.batch(['a'],source,output,settings())
            self.assertFalse(output.exists(), 'Output must wait for all template compatibility checks')
            with self.assertRaises(ValueError):app.batch(['a'],source,'',settings())

    def test_defaults_and_preferences_preserve_custom_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); app = Service(root / 'data')
            self.assertEqual(app.preferences['settings']['fit'], 'auto')
            self.assertEqual(app.preferences['settings']['width'], 0)
            output = root / 'new' / 'output'
            result = app.save_preferences(dict(input='', output=str(output), settings=settings(width=600, height=900)))
            self.assertTrue(result['ok'])
            self.assertTrue(output.is_dir())
            self.assertEqual(Service(root / 'data').preferences['settings']['height'], 900)

    def test_preferences_reject_source_as_output_before_creating_directories(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); app = Service(root / 'data')
            with self.assertRaisesRegex(ValueError, '分开'):
                app.save_preferences(dict(input=str(root), output=str(root), settings=settings()))
            app.task['status'] = 'running'
            with self.assertRaises(ValueError):
                app.save_preferences(dict(input='', output='', settings=settings()))

    def test_artworks_are_naturally_sorted_bounded_and_originals_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / 'source'; source.mkdir(); app = Service(root / 'data')
            for name in ('art10.png', 'art2.png', 'art1.png'):
                Image.new('RGBA', (400,100), (255,0,0,128)).save(source / name)
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            page = app.artworks(source, offset=1, limit=1)
            self.assertEqual(page['total'], 3)
            self.assertEqual(page['files'][0]['name'], 'art2.png')
            item = page['files'][0]
            self.assertTrue(item['has_alpha'])
            self.assertEqual((item['width'],item['height']), (400,100))
            with Image.open(app.root / item['url'].removeprefix('/media/')) as thumb:
                self.assertLessEqual(max(thumb.size),220)
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
            with self.assertRaises(ValueError): app.artworks(source, offset=-1)
            self.assertLessEqual(len(app.artworks(source, limit=1000)['files']), 100)

    def test_open_output_only_allows_existing_recorded_directories(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); app=Service(root/'data'); out=root/'out'; out.mkdir(); other=root/'other'; other.mkdir()
            app.preferences['output']=str(out)
            with patch('service.os.startfile', create=True) as start:
                self.assertTrue(app.open_output(str(out))['ok'])
                start.assert_called_once_with(str(out.resolve()))
                with self.assertRaises(ValueError): app.open_output(str(other))


if __name__ == '__main__':
    unittest.main()
