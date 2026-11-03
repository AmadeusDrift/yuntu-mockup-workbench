import json
import tempfile
import unittest
from pathlib import Path


class CatalogTests(unittest.TestCase):
    def test_non_numbered_helper_groups_remain_common(self):
        import catalog
        class Layer(list):
            def __init__(self,name,children=None):
                super().__init__(children or []);self.name=name;self.kind='group' if children is not None else 'smartobject';self.tagged_blocks={}
            def is_group(self):return self.kind=='group'
        doc=Layer('doc',[Layer('Background',[]),Layer('Front',[Layer('PRODUCT')]),Layer('Back',[Layer('PRODUCT')])])
        self.assertEqual([l.name for _,l in catalog.scene_nodes(doc)],['Front','Back'])

    def test_same_source_instances_include_detail_but_exclude_background(self):
        import catalog
        layers = [dict(id='m', name='母稿', source='a', root=True),
                  dict(id='1', name='PRINT_1', source='a'),
                  dict(id='2', name='DETAIL_2', source='a'),
                  dict(id='3', name='FIXED', source='b')]
        self.assertEqual(catalog.detect_targets(layers), {'1', '2'})

    def test_no_guessing_arbitrary_smart_objects(self):
        import catalog
        self.assertEqual(catalog.detect_targets([dict(id='1', name='背景', source='a')]), set())

    def test_ambiguous_multiple_mothers_requires_manual_mapping(self):
        import catalog
        layers = [dict(id='1', name='母稿', source='a', root=True),
                  dict(id='2', name='母稿', source='b', root=True)]
        self.assertEqual(catalog.detect_targets(layers), set())

    def test_scene_settings_cannot_select_other_scene_or_unknown_mode(self):
        import catalog
        template = {'scenes':[{'id':'1','candidates':[{'id':'a'}],'targets':['a'],'mode':'replace'}]}
        with self.assertRaises(ValueError):
            catalog.updated_scenes(template, [{'id':'1','mode':'replace','targets':['other']}])
        with self.assertRaises(ValueError):
            catalog.updated_scenes(template, [{'id':'1','mode':'wat','targets':[]}])


class CacheTests(unittest.TestCase):
    def test_summary_does_not_wait_for_a_template_build(self):
        import cache
        import threading
        with tempfile.TemporaryDirectory() as td:
            store=cache.DiskCache(Path(td),limit_bytes=100000)
            gate=threading.Event();started=threading.Event();finished=threading.Event()
            def build(folder):
                started.set();gate.wait(2);(folder/'template.json').write_text('{}')
            worker=threading.Thread(target=lambda:store.ensure({'name':'test','digest':'a','scenes':[]},build));worker.start()
            started.wait(1)
            reader=threading.Thread(target=lambda:(store.summary(),finished.set()));reader.start()
            try:self.assertTrue(finished.wait(.5))
            finally:gate.set();worker.join();reader.join()

    def test_cache_reuses_unchanged_template_and_invalidates_changed_settings(self):
        import cache
        with tempfile.TemporaryDirectory() as td:
            store = cache.DiskCache(Path(td), limit_bytes=100000)
            template = {'name':'test','digest':'abc','scenes':[{'id':'1','mode':'replace','targets':['x']}]}
            calls=[]
            def build(folder):
                calls.append(1)
                (folder/'template.json').write_text('{}')
            first=store.ensure(template, build)
            self.assertEqual(store.ensure(template, build), first)
            self.assertEqual(len(calls),1)
            template['scenes'][0]['mode']='fixed'
            self.assertNotEqual(store.ensure(template,build),first)
            self.assertEqual(len(calls),2)

    def test_clean_preserves_active_cache_and_never_touches_source(self):
        import cache
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source.psd';source.write_bytes(b'original')
            store=cache.DiskCache(root/'cache',limit_bytes=100000)
            template={'name':'test','digest':'abc','scenes':[]}
            manifest=store.ensure(template,lambda folder:(folder/'template.json').write_text('{}'))
            with store.use(manifest):
                store.clean()
                self.assertTrue(manifest.exists())
            store.clean()
            self.assertFalse(manifest.exists())
            self.assertEqual(source.read_bytes(),b'original')

    def test_failed_build_is_not_reused(self):
        import cache
        with tempfile.TemporaryDirectory() as td:
            store=cache.DiskCache(Path(td),limit_bytes=100000)
            def broken(folder):
                (folder/'partial').write_bytes(b'x')
                raise ValueError('unsupported layer')
            with self.assertRaises(ValueError):
                store.ensure({'name':'test','digest':'abc','scenes':[]},broken)
            self.assertEqual(store.summary()['entries'],[])


if __name__ == '__main__':
    unittest.main()
