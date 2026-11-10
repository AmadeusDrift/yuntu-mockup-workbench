"""Isolated desktop integration fixture; optional first argument selects a built release."""
import json, os, sys, subprocess, time
from pathlib import Path
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'workbench'))
import bootstrap, catalog
from cache import cache_key
from PIL import Image, ImageDraw
sys.path.insert(0, str(root / 'workbench/desktop'))
import installer_helper
version = json.loads((root / 'workbench/desktop/package.json').read_text(encoding='utf-8'))['version']
target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / ('release/套图工作台-v' + version)
assert (target / '套图工作台.exe').is_file(), target
run = root / '.analysis-temp' / ('placement-desktop-' + time.strftime('%Y%m%d-%H%M%S'))
run.mkdir(parents=True)
data, src, output = (run / name for name in ('data', 'input', 'output'))
for folder in (data, src, output): folder.mkdir()
image = Image.new('RGB', (240, 120), '#8196ac')
draw = ImageDraw.Draw(image)
for x, color in ((0, '#cc4433'), (80, '#448855'), (160, '#3355cc')): draw.rectangle((x, 0, x + 79, 119), fill=color)
image.save(src / '彩色宽幅.png')
psd = run / 'fixture.psd'; psd.write_bytes(b'Synthetic cached renderer fixture; not a PSD import test.')
sig = catalog.fingerprint(psd)
scenes = [dict(id=str(i), name=str(i+1), mode='replace', targets=['a'], candidates=[dict(id='a', name='print', path='print')], thumbnail=None) for i in range(3)]
template = dict(id='fixture', name='印花拖动缩放测试', path=str(psd), digest=sig['sha256'], signature=sig, canvas=[120, 120], scenes=scenes, warnings=[])
(data / 'templates.json').write_text(json.dumps([template]), encoding='utf-8')
# Deliberately omit placement settings to verify backward-compatible defaults.
settings = dict(width=0, height=0, ppi=72, quality=100, fit='cover', format='png', trim=False, fit_policy=2, orientation='original', rotation=0, brightness=0, input_layout='folders', input_exclude=[], naming=dict(folder='{name}', image='{scene:02}', start=1))
(data / 'preferences.json').write_text(json.dumps(dict(input=str(src), output=str(output), settings=settings, limit=0, preferences_version=2)), encoding='utf-8')
cache = data / 'cache' / cache_key(template); cache.mkdir(parents=True)
ops = [dict(kind='slot', native_size=[120,120], quad=[[0,0],[120,0],[120,120],[0,120]], opacity=255)]
(cache / 'template.json').write_text(json.dumps(dict(version=4, workbench=True, scenes=[dict(name=str(i+1), width=120, height=120, slots=1, ops=ops) for i in range(3)])), encoding='utf-8')
(cache / 'cache-meta.json').write_text(json.dumps(dict(name='fixture', last_used=time.time())), encoding='utf-8')
env = dict(os.environ); env.pop('ELECTRON_RUN_AS_NODE', None)
log = (run / 'desktop.log').open('wb')
process = subprocess.Popen([str(target / '套图工作台.exe'), '--data-dir=' + str(data), '--remote-debugging-port=9343'], env=env, stdout=log, stderr=log)
try:
    for _ in range(150):
        if (data / 'desktop-status.json').exists(): break
        if process.poll() is not None: raise RuntimeError('Desktop exited')
        time.sleep(.2)
    checked = subprocess.run([str(Path.home() / 'AppData/Local/Programs/node-v24.21.0-win-x64/node.exe'), str(root / 'workbench/desktop/test-placement.cjs'), str(data), str(run)], timeout=180, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
    (run / 'node-test.log').write_text(checked.stdout, encoding='utf-8')
    print(checked.stdout, flush=True)
    checked.check_returncode()
    result = json.loads((run / 'ui-result.json').read_text(encoding='utf-8'))
    report = dict(status='PASS', version=version, run=str(run), fixture='synthetic cached renderer', **result)
    (run / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: placement desktop integration: ' + str(run))
finally:
    installer_helper.close_applications(target, data, 90)
    process.wait(timeout=15)
    log.close()
