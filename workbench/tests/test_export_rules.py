import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import catalog
import engine
from service import Service


def make_batch(root, names=('art10.png', 'art2.png', 'art1.png')):
    source = root / 'input'; source.mkdir()
    output = root / 'output'; output.mkdir()
    cache = root / 'cache'; cache.mkdir()
    for name in names:
        Image.new('RGB', (24, 24), 'green').save(source / name)
    scenes = []
    for name, color in [('7', 'red'), ('2', 'blue'), ('4', 'yellow')]:
        Image.new('RGBA', (24, 24), color).save(cache / (name + '.png'))
        scenes.append(dict(name=name, width=24, height=24, slots=0, fixed=True,
                           ops=[dict(kind='static', asset=name + '.png')]))
    manifest = cache / 'template.json'
    manifest.write_text(json.dumps(dict(version=4, workbench=True, scenes=scenes)))
    settings = dict(width=0, height=0, ppi=72, quality=95, fit='auto', format='png',
                    naming=dict(folder='{index:03}', image='{scene:02}', start=1))
    return manifest, source, output, settings


class ExportRuleTests(unittest.TestCase):
    def test_numbered_folders_are_deterministic_and_images_follow_scene_order(self):
        import parallel_batch
        with tempfile.TemporaryDirectory() as td:
            manifest, source, output, settings = make_batch(Path(td))
            original = parallel_batch.prepare_product
            def delayed(renderer, path, *args):
                if Path(path).name == 'art1.png': time.sleep(.05)
                return original(renderer, path, *args)
            with patch.object(parallel_batch, 'prepare_product', delayed):
                dest, state = engine.batch(manifest, source, output, settings)
            self.assertEqual(state['records']['art1.png']['outputs'], [str(Path('001') / f'{i:02}.png') for i in (1, 2, 3)])
            self.assertEqual(state['records']['art10.png']['outputs'][0], str(Path('003') / '01.png'))
            for index, color in [(1, (255, 0, 0, 255)), (2, (0, 0, 255, 255)), (3, (255, 255, 0, 255))]:
                with Image.open(dest / '001' / f'{index:02}.png') as image:
                    self.assertEqual(image.getpixel((12, 12)), color)
            before = {p: p.stat().st_mtime_ns for p in dest.glob('*/*.png')}
            engine.batch(manifest, source, output, settings, resume=dest)
            self.assertTrue(all(p.stat().st_mtime_ns == stamp for p, stamp in before.items()))
            changed = copy.deepcopy(settings); changed['naming']['start'] = 20
            with self.assertRaisesRegex(ValueError, '不能继续'):
                engine.batch(manifest, source, output, changed, resume=dest)

    def test_source_folder_and_custom_prefixes_and_start(self):
        with tempfile.TemporaryDirectory() as td:
            manifest, source, output, settings = make_batch(Path(td), ('flower.png',))
            settings['naming'] = dict(folder='{name}', image='{scene:02}', start=1)
            dest, _ = engine.batch(manifest, source, output, settings)
            self.assertTrue((dest / 'flower' / '01.png').is_file())
            settings['naming'] = dict(folder='ITEM_{index:04}', image='{folder}_{scene:02}', start=12)
            dest, _ = engine.batch(manifest, source, output, settings)
            self.assertTrue((dest / 'ITEM_0012' / 'ITEM_0012_01.png').is_file())

    def test_numbering_allows_same_stem_with_different_extensions(self):
        with tempfile.TemporaryDirectory() as td:
            manifest, source, output, settings = make_batch(Path(td), ('same.png', 'same.jpg'))
            dest, state = engine.batch(manifest, source, output, settings)
            self.assertEqual({Path(r['outputs'][0]).parts[0] for r in state['records'].values()}, {'001', '002'})

    def test_legacy_settings_keep_existing_naming_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            manifest, source, output, settings = make_batch(Path(td), ('flower.png',))
            del settings['naming']
            dest, state = engine.batch(manifest, source, output, settings)
            self.assertTrue((dest / 'flower' / 'flower_01.png').is_file())
            self.assertEqual(state['identity']['naming_schema'], 'source_sequence_v1')
            engine.batch(manifest, source, output, settings, resume=dest)

    def test_invalid_rules_and_collisions_fail_before_publishing(self):
        for folder, image in [('../{index}', '{scene:02}'), ('{unknown}', '{scene}'), ('{name}', '{scene.__class__}'), ('{name}', '{scene:10000}')]:
            with self.subTest(folder=folder, image=image), self.assertRaises(ValueError):
                engine.settings_checked(dict(width=0, height=0, ppi=72, quality=95, fit='auto', format='png', naming=dict(folder=folder, image=image, start=1)))
        with tempfile.TemporaryDirectory() as td:
            manifest, source, output, settings = make_batch(Path(td))
            settings['naming']['image'] = '{name}'
            with self.assertRaisesRegex(ValueError, '重名'):
                engine.batch(manifest, source, output, settings)
            self.assertEqual(list(output.iterdir()), [])

    def test_scene_order_is_saved_and_kept_after_reimport(self):
        with tempfile.TemporaryDirectory() as td:
            app = Service(Path(td) / 'data')
            template = dict(id='a', name='Template', digest='abc', scenes=[dict(id=str(i), name=str(i), mode='fixed', targets=[], candidates=[]) for i in (1, 2, 3)])
            app.templates = [copy.deepcopy(template)]
            reordered = [template['scenes'][i] for i in (2, 0, 1)]
            app.configure('a', reordered)
            self.assertEqual([s['id'] for s in Service(app.root).templates[0]['scenes']], ['3', '1', '2'])
            with patch.object(catalog, 'inspect', return_value=copy.deepcopy(template)):
                app.scan(['template.psd'])
                for _ in range(200):
                    if app.task['status'] not in ('running', 'paused', 'stopping'): break
                    time.sleep(.01)
            self.assertEqual([s['id'] for s in app.templates[0]['scenes']], ['3', '1', '2'])
