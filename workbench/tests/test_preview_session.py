"""Interactive reuse must preserve exact output and detect changed source files."""
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
from cache import cache_key
from service import Service


class PreviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root / 'data')
        self.psd = self.root / 'fixture.psd'
        self.psd.write_bytes(b'cached fixture: original PSD bytes')
        sig = catalog.fingerprint(self.psd)
        self.template = dict(id='t', name='fixture', path=str(self.psd), digest=sig['sha256'], signature=sig,
                             scenes=[dict(id=str(i), name=str(i+1), mode='replace', targets=['a'],
                                          candidates=[dict(id='a', name='print')]) for i in range(2)])
        self.service.templates = [self.template]
        folder = self.service.root / 'cache' / cache_key(self.template)
        folder.mkdir(parents=True)
        self.manifest = folder / 'template.json'
        Image.new('RGBA', (80, 80), '#abcdef').save(folder / 'background.png')
        slot = dict(kind='slot', native_size=[80, 80], quad=[[0,0],[80,0],[80,80],[0,80]])
        self.manifest.write_text(json.dumps(dict(version=4, workbench=True, scenes=[
            dict(name=str(i+1), width=80, height=80, ops=[dict(kind='static', asset='background.png'), slot]) for i in range(2)])), encoding='utf-8')
        (folder / 'cache-meta.json').write_text(json.dumps(dict(name='fixture', last_used=time.time())))
        self.input = self.root / 'input'
        self.input.mkdir()
        self.sources = [self.input / 'a.png', self.input / 'b.png']
        for path, color in zip(self.sources, ('#996633', '#336699')):
            Image.new('RGBA', (160, 80), color).save(path)
        self.settings = dict(width=0, height=0, quality=90, ppi=72, fit='cover', format='png', orientation='original',
                             scale=75, offset_x=12, offset_y=-8, brightness=18,
                             naming=dict(folder='{name}', image='{scene:02}', start=1))

    def tearDown(self):
        if hasattr(self.service, 'close'):
            self.service.close()
        self.temp.cleanup()

    def wait(self, status='done'):
        deadline = time.monotonic() + 10
        while self.service.task['status'] in ('running', 'stopping') and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.service.task['status'], status, self.service.task.get('error'))

    def preview(self, source=0, **settings):
        self.service.preview('t', str(self.sources[source]), dict(self.settings, **settings), scene=0)
        self.wait()
        path = self.service.root / self.service.task['preview'][0]['url'].removeprefix('/media/')
        with Image.open(path) as image:
            return image.copy()

    def test_adjustments_and_source_switch_reuse_one_verified_template(self):
        constructed = []
        initialize = engine.Renderer.__init__
        def record(renderer, *args, **kwargs):
            constructed.append(1)
            initialize(renderer, *args, **kwargs)
        with patch.object(catalog, 'fingerprint', wraps=catalog.fingerprint) as fingerprint, \
                patch.object(engine.Renderer, '__init__', record):
            self.service.editor('t', str(self.sources[0]), self.settings)
            self.wait()
            self.preview().close()
            self.preview(scale=90).close()
            self.preview(1, brightness=-12).close()
            self.assertEqual(fingerprint.call_count, 1, 'An unchanged PSD must not be read again for every edit')
            self.assertEqual(len(constructed), 1, 'Decoded template assets must stay warm across previews')

    def test_changed_psd_is_rejected_after_a_warm_preview(self):
        self.preview().close()
        self.psd.write_bytes(b'PSD externally edited; cached geometry is now stale')
        self.service.preview('t', str(self.sources[0]), self.settings, scene=0)
        self.wait('failed')
        self.assertIn('PSD 已修改', self.service.task['error'])

    def test_changed_manifest_replaces_warm_renderer(self):
        self.preview().close()
        data = json.loads(self.manifest.read_text())
        data['scenes'][0]['name'] = 'modified scene'
        self.manifest.write_text(json.dumps(data))
        self.preview().close()
        self.assertEqual(self.service.task['preview'][0]['name'], 'modified scene')

    def test_batch_rechecks_psd_and_uses_global_settings_identical_to_preview(self):
        previews = [self.preview(i) for i in range(2)]
        with patch.object(catalog, 'fingerprint', wraps=catalog.fingerprint) as fingerprint:
            self.service.batch(['t'], str(self.input), str(self.root / 'output'), self.settings)
            self.wait()
            self.assertGreaterEqual(fingerprint.call_count, 1, 'Export must independently validate the PSD')
        for source, expected in zip(self.sources, previews):
            outputs = list((self.root / 'output').rglob(source.stem + '/01.png'))
            self.assertEqual(len(outputs), 1)
            with Image.open(outputs[0]) as actual:
                self.assertEqual(actual.convert('RGBA').tobytes(), expected.convert('RGBA').tobytes())
            expected.close()

    def test_preferences_can_change_during_preview_but_not_batch(self):
        self.service.task.update(status='running', kind='preview')
        try:
            self.service.save_preferences(dict(settings=self.settings))
        except ValueError as exc:
            self.fail('Preview settings snapshots allow newer preferences: ' + str(exc))
        self.assertEqual(self.service.preferences['settings']['scale'], 75)
        self.service.task['kind'] = 'batch'
        with self.assertRaises(ValueError):
            self.service.save_preferences(dict(settings=self.settings))
        self.service.task.update(status='done', kind='')

    def test_cache_cleanup_releases_the_retained_renderer(self):
        self.preview().close()
        self.assertIn(self.manifest.parent.name, self.service.cache.active,
                      'Resident renderer must pin its disk template until release')
        self.service.cache_command('clean')
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.service.cache.active)

    def test_nested_cache_users_do_not_unpin_each_other(self):
        key = self.manifest.parent.name
        with self.service.cache.use(self.manifest):
            with self.service.cache.use(self.manifest):
                pass
            self.assertIn(key, self.service.cache.active)
            self.service.cache.clean()
            self.assertTrue(self.manifest.exists())

    def test_lightweight_task_snapshot_avoids_cache_scan(self):
        self.assertTrue(callable(getattr(self.service, 'task_state', None)), 'Lightweight task polling is required')
        with patch.object(self.service.cache, 'summary', side_effect=AssertionError('Unexpected cache scan')):
            first = self.service.task_state()
            self.service.update(message='new frame')
            second = self.service.task_state()
        self.assertGreater(second['snapshot_id'], first['snapshot_id'])
        self.assertEqual(second['task']['message'], 'new frame')
        self.assertNotEqual(first['task']['message'], 'new frame')

    def test_draft_uses_bounded_output_and_full_preview_keeps_export_dimensions(self):
        import inspect
        self.assertIn('draft', inspect.signature(self.service.preview).parameters)
        settings = dict(self.settings, width=2400, height=1200)
        self.service.preview('t', str(self.sources[0]), settings, scene=1, draft=True)
        self.wait()
        self.assertEqual(self.service.task['preview_quality'], 'draft')
        self.assertEqual(self.service.task['preview_settings']['width'], 2400)
        path = self.service.root / self.service.task['preview'][0]['url'].removeprefix('/media/')
        with Image.open(path) as image:
            self.assertEqual(image.size, (720,360))
        self.service.preview('t', str(self.sources[0]), settings, scene=1)
        self.wait()
        self.assertEqual(self.service.task['preview_quality'], 'full')
        path = self.service.root / self.service.task['preview'][0]['url'].removeprefix('/media/')
        with Image.open(path) as image:
            self.assertEqual(image.size, (2400,1200))

    def test_memory_budget_releases_an_oversized_renderer_after_use(self):
        self.service.preview_session.limit_bytes = 1
        self.preview().close()
        self.assertEqual(self.service.preview_session.retained_bytes, 0)
        self.assertFalse(self.service.cache.active)

    def test_close_tolerates_a_removed_cache_directory(self):
        self.preview().close()
        self.temp.cleanup()
        self.service.close()
        self.assertEqual(self.service.preview_session.retained_bytes, 0)

    def test_draft_requires_a_scene_and_boolean_quality_flag(self):
        import inspect
        self.assertIn('draft', inspect.signature(self.service.preview).parameters)
        for scene, draft in ((None, True), (0, 'true'), (0, 1)):
            with self.assertRaises(ValueError):
                self.service.preview('t', str(self.sources[0]), self.settings, scene=scene, draft=draft)
