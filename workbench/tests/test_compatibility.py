import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import compatibility
import catalog
from service import Service


def template():
    return {'id':'x','name':'fixture','path':'missing.psd','digest':'abc',
            'scenes':[{'id':'s','name':'1','mode':'replace','targets':['a'],'candidates':[{'id':'a'}]}]}


class CompatibilityTests(unittest.TestCase):
    def audit_layers(self, layers):
        class Document(list):
            __hash__=object.__hash__
            size=(4,4)
        psd=Document(layers)
        item=template();item['scenes']=[dict(id='document',name='1',mode='fixed',targets=[])]
        return catalog.audit(psd,item)

    def test_all_visible_mask_blockers_are_reported_together(self):
        from types import SimpleNamespace as NS
        from test_effects import Layer
        layers=[]
        for name in ('mask-A','mask-B'):
            layer=Layer(name);layer.has_mask=lambda:True
            layer.mask=NS(disabled=False,flags=NS(invert_mask=False,pos_relative_to_layer=False),
                          parameters=NS(user_mask_density=128,user_mask_feather=0))
            layers.append(layer)
        result=self.audit_layers(layers)
        self.assertEqual(len(result['issues']),2)
        self.assertTrue(any('mask-A' in i['message'] for i in result['issues']))
        self.assertTrue(any('mask-B' in i['message'] for i in result['issues']))

    def test_inactive_clipped_unknown_effect_does_not_block(self):
        from types import SimpleNamespace as NS
        from test_effects import Layer
        for hidden in (False,True):
            base=Layer('empty');base.width=base.height=0;base.topil=lambda:None
            base._record.channel_info=[NS(length=2)];base.visible=not hidden
            effect=Layer('ignored',kind='selectivecolor',clipping=True)
            result=self.audit_layers([base,effect])
            self.assertEqual(result['status'],'ready')

    def test_multiple_unsupported_effects_are_reported_but_hidden_effect_is_ignored(self):
        from test_effects import Layer
        hidden=Layer('hidden',kind='selectivecolor');hidden.visible=False
        result=self.audit_layers([Layer('unknown-A',kind='colorlookup'),hidden,Layer('unknown-B',kind='selectivecolor')])
        self.assertEqual(len(result['issues']),2)

    def test_report_bound_to_source_and_scene_configuration(self):
        original=template()
        original['compatibility']=compatibility.report(original,[{'scene':'s','message':'layer: unsupported'}])
        with self.assertRaisesRegex(ValueError,'layer: unsupported'):compatibility.require(original)
        for change in ('digest','mode','targets','artwork_roles'):
            item=copy.deepcopy(original)
            if change=='digest':item['digest']='new'
            elif change=='mode':item['scenes'][0]['mode']='skip'
            elif change=='targets':item['scenes'][0]['targets']=['b']
            else:item['scenes'][0]['artwork_roles']={'a':'left'}
            self.assertIsNone(compatibility.current(item))
            compatibility.require(item)

    def test_known_blocker_rejected_before_any_job_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Service(folder)
            item=template();item['compatibility']=compatibility.report(item,[{'scene':'s','message':'effect unsupported'}])
            service.templates=[item]
            with patch.object(service,'start') as start:
                for method,args in ((service.editor,('x','missing.png',{})),
                                    (service.preview,('x','missing.png',{})),
                                    (service.overview,('x',['missing.png'],{})),
                                    (service.batch,(['x'],'missing','output',{}))):
                    with self.subTest(method=method.__name__):
                        with self.assertRaisesRegex(ValueError,'effect unsupported'):method(*args)
                start.assert_not_called()

    def test_report_collects_all_enabled_scenes_including_fixed(self):
        item=template();item['scenes'] += [dict(id='f',name='2',mode='fixed',targets=[]),dict(id='z',name='3',mode='skip',targets=[])]
        with patch.object(catalog,'compile_scene',side_effect=[ValueError('1/unsupported A'),ValueError('2/unsupported B')]) as compile_scene:
            result=catalog.audit(None,item)
        self.assertEqual(compile_scene.call_count,2)
        self.assertEqual([i['scene'] for i in result['issues']],['s','f'])
        self.assertEqual(result['status'],'blocked')

    def test_warnings_do_not_block_a_supported_template(self):
        item=template();item['compatibility']=compatibility.report(item,[],['approximate'])
        self.assertEqual(item['compatibility']['status'],'review')
        compatibility.require(item)

    def test_later_invalid_template_blocks_batch_before_first_output(self):
        import time
        from PIL import Image
        from service import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'input';source.mkdir();output=root/'output'
            Image.new('RGB',(4,4),'red').save(source/'art.png')
            service=Service(root/'data')
            first=template();second=copy.deepcopy(first);second.update(id='y',name='second')
            service.templates=[first,second]
            with patch.object(service,'manifest',side_effect=[root/'ok.json',ValueError('second unsupported')]),patch('engine.batch') as batch:
                service.batch(['x','y'],source,output,copy.deepcopy(DEFAULTS['settings']))
                for _ in range(300):
                    if service.task['status'] not in ('running','stopping'):break
                    time.sleep(.01)
                self.assertEqual(service.task['status'],'failed')
                self.assertIn('second unsupported',service.task['error'])
                self.assertFalse(output.exists());batch.assert_not_called()
