import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HELPER=Path(__file__).resolve().parents[1]/'desktop'/'installer_helper.py'


class InstallerUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.payload=self.root/'payload';self.target=self.root/'custom install';self.data=self.root/'userdata'
        for folder in (self.payload,self.target,self.data):folder.mkdir()
        self.make_app(self.payload,'0.2.6',{'套图工作台.exe':b'new exe','resources/app/core.py':b'new core'})
        self.make_app(self.target,'0.2.5',{'套图工作台.exe':b'old exe','resources/app/obsolete.py':b'old module'})
        (self.target/'my-photo.jpg').write_bytes(b'user output')
        (self.data/'templates.json').write_text('[{"id":"keep-template"}]')
        (self.data/'preferences.json').write_text('{"rotation":45}')
        (self.data/'cache').mkdir();(self.data/'cache'/'large.bin').write_bytes(b'cache not copied')

    def helper(self):
        self.assertTrue(HELPER.exists(),'Missing transactional update helper')
        spec=importlib.util.spec_from_file_location('installer_helper_test',HELPER)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def make_app(self,folder,version,files):
        files=dict(files)
        files['resources/app/package.json']=json.dumps(dict(name='yuntu-mockup-workbench',version=version)).encode()
        for name,content in files.items():
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        import hashlib
        entries=[dict(path=name,sha256=hashlib.sha256(content).hexdigest(),bytes=len(content)) for name,content in files.items()]
        manifest=folder/'resources/app/install-manifest.json'
        manifest.write_text(json.dumps(dict(product='yuntu-mockup-workbench',version=version,files=entries)))

    def test_upgrade_keeps_user_files_and_settings_backs_up_configuration_and_removes_obsolete_code(self):
        module=self.helper()
        result=module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'new exe')
        self.assertFalse((self.target/'resources/app/obsolete.py').exists())
        self.assertEqual((self.target/'my-photo.jpg').read_bytes(),b'user output')
        self.assertIn('keep-template',(self.data/'templates.json').read_text())
        backup=Path(result['settings_backup'])
        self.assertEqual((backup/'preferences.json').read_bytes(),(self.data/'preferences.json').read_bytes())
        self.assertFalse((backup/'cache').exists())
        self.assertFalse((self.target/module.MARKER).exists())

    def test_file_failure_restores_previous_program_and_does_not_touch_settings(self):
        module=self.helper();updater=module.Updater(self.payload,self.target,self.data)
        original=module.replace_file
        def fail_once(source,target):
            if source==self.payload/'resources/app/core.py':raise OSError('simulated disk failure')
            return original(source,target)
        with patch.object(module,'replace_file',side_effect=fail_once):
            with self.assertRaisesRegex(module.UpdateError,'恢复'):updater.install()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'old exe')
        self.assertTrue((self.target/'resources/app/obsolete.py').exists())
        self.assertFalse((self.target/'resources/app/core.py').exists())
        self.assertEqual(json.loads((self.target/'resources/app/package.json').read_text())['version'],'0.2.5')

    def test_downgrade_is_rejected_without_changing_files(self):
        module=self.helper()
        self.make_app(self.target,'0.2.7',{'套图工作台.exe':b'newer exe'})
        with self.assertRaisesRegex(module.UpdateError,'旧版本'):module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'newer exe')

    def test_same_version_can_repair_and_legacy_install_without_inventory_can_upgrade(self):
        module=self.helper()
        (self.target/'resources/app/install-manifest.json').unlink()
        module.Updater(self.payload,self.target,self.data).install()
        (self.target/'套图工作台.exe').write_bytes(b'broken')
        module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'new exe')

    def test_modified_payload_and_unrelated_target_are_rejected(self):
        module=self.helper()
        (self.payload/'套图工作台.exe').write_bytes(b'tampered')
        with self.assertRaises(module.UpdateError):module.Updater(self.payload,self.target,self.data).install()
        (self.target/'resources/app/package.json').write_text('{"name":"another-product","version":"1.0.0"}')
        with self.assertRaises(module.UpdateError):module.Updater(self.payload,self.target,self.data).install()

    def test_incomplete_update_can_be_recovered_on_next_installer_run(self):
        module=self.helper();updater=module.Updater(self.payload,self.target,self.data)
        def power_loss(source,target):
            if source==self.payload/'resources/app/core.py':raise KeyboardInterrupt('simulated power loss')
            return original(source,target)
        original=module.replace_file
        with patch.object(module,'replace_file',side_effect=power_loss):
            with self.assertRaises(KeyboardInterrupt):updater.install()
        self.assertTrue((self.target/module.MARKER).exists())
        updater.recover()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'old exe')
        self.assertFalse((self.target/module.MARKER).exists())

    def test_malicious_manifest_paths_never_write_outside_target(self):
        module=self.helper()
        path=self.payload/'resources/app/install-manifest.json';manifest=json.loads(path.read_text())
        manifest['files'][0]['path']='../outside.txt';path.write_text(json.dumps(manifest))
        with self.assertRaises(module.UpdateError):module.Updater(self.payload,self.target,self.data).install()
        self.assertFalse((self.root/'outside.txt').exists())

    def test_running_application_requires_explicit_close_permission(self):
        module=self.helper()
        with patch.object(module,'running_processes',return_value={123:'app'}):
            with self.assertRaisesRegex(module.UpdateError,'运行'):module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'old exe')

    def test_transient_windows_file_lock_is_retried_before_giving_up(self):
        module=self.helper();real_replace=module.os.replace;attempts=[]
        def briefly_locked(source,target):
            attempts.append(target)
            if len(attempts)==1:raise PermissionError(13,'temporary antivirus lock')
            return real_replace(source,target)
        with patch.object(module.os,'replace',side_effect=briefly_locked):
            module.replace_file(self.payload/'套图工作台.exe',self.target/'套图工作台.exe')
        self.assertEqual(len(attempts),2)
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'new exe')

    def test_application_started_during_backup_blocks_before_any_program_write(self):
        module=self.helper()
        with patch.object(module,'running_processes',side_effect=[{}, {}, {123:'app'}]):
            with self.assertRaisesRegex(module.UpdateError,'运行'):module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual((self.target/'套图工作台.exe').read_bytes(),b'old exe')
        self.assertFalse((self.target/module.MARKER).exists())

    def test_case_only_filename_change_is_not_deleted_as_obsolete_on_windows(self):
        module=self.helper()
        self.make_app(self.target,'0.2.5',{'套图工作台.exe':b'old exe','resources/app/core.py':b'old core'})
        self.make_app(self.payload,'0.2.6',{'套图工作台.exe':b'new exe','resources/app/CORE.py':b'new core'})
        module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual((self.target/'resources/app/CORE.py').read_bytes(),b'new core')

    def test_failed_first_install_can_retry_the_same_directory(self):
        module=self.helper();target=self.root/'new install';updater=module.Updater(self.payload,target,self.data)
        original=module.replace_file
        def fail_once(source,destination):
            if source==self.payload/module.MANIFEST:raise OSError('disk failed')
            return original(source,destination)
        with patch.object(module,'replace_file',side_effect=fail_once):
            with self.assertRaises(module.UpdateError):updater.install()
        updater.install()
        self.assertEqual((target/'套图工作台.exe').read_bytes(),b'new exe')

    def test_backup_and_replacement_support_long_windows_paths(self):
        module=self.helper()
        target=self.root/('a'*60)/('b'*60)/('c'*60)/('d'*60)/'library.dll'
        import shutil
        self.addCleanup(lambda:shutil.rmtree(module.native_path(self.root/('a'*60)),ignore_errors=True))
        module.replace_file(self.payload/'套图工作台.exe',target)
        self.assertEqual(Path('\\\\?\\'+str(target)).read_bytes(),b'new exe')

    def test_identical_dependencies_keep_their_files_and_do_not_fill_program_backup(self):
        module=self.helper()
        self.make_app(self.target,'0.2.5',{'套图工作台.exe':b'old exe','resources/app/stable.dll':b'unchanged dependency'})
        self.make_app(self.payload,'0.2.6',{'套图工作台.exe':b'new exe','resources/app/stable.dll':b'unchanged dependency'})
        stable=self.target/'resources/app/stable.dll';before=stable.stat().st_mtime_ns
        with patch.object(module,'replace_file',wraps=module.replace_file) as copied:
            module.Updater(self.payload,self.target,self.data).install()
        self.assertEqual(stable.stat().st_mtime_ns,before)
        self.assertFalse(any(Path(call.args[0]).name=='stable.dll' for call in copied.call_args_list))
