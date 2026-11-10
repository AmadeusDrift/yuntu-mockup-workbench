"""Read existing real PSD fixtures; measure warm previews and compare exact exports."""
import hashlib
import importlib.util
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'workbench'))
import bootstrap, catalog, engine
from cache import cache_key
from service import Service
from PIL import Image

fixture = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'.analysis-temp/transfer-check-20261109-213600'
baseline = root/'.analysis-temp/live-preview-baseline-20261110-150201/workbench/service.py'
run = root/'.analysis-temp'/('live-preview-real-'+time.strftime('%Y%m%d-%H%M%S'))
run.mkdir()
tempfile.tempdir = str(run)
templates = json.loads((fixture/'userdata/templates.json').read_text(encoding='utf-8'))
settings = json.loads((fixture/'userdata/preferences.json').read_text(encoding='utf-8'))['settings']
settings.update(width=0,height=0,format='png',scale=75,offset_x=12,offset_y=-8,brightness=18,
                naming=dict(folder='{name}',image='{scene:02}',start=1),input_layout='flat',input_exclude=[])
source = next(p for p in (fixture/'assets').rglob('*') if p.suffix.lower() in ('.png','.jpg','.jpeg') and p.is_file())
inputs = run/'input';inputs.mkdir()
shutil.copyfile(source,inputs/('A'+source.suffix))
with Image.open(source) as image:
    turned=image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    turned.save(inputs/'B.png');turned.close()
artworks=sorted(inputs.iterdir())
original_hashes={t['path']:catalog.fingerprint(t['path'])['sha256'] for t in templates}

def prepare_data(name):
    data=run/name;data.mkdir()
    (data/'templates.json').write_text(json.dumps(templates),encoding='utf-8')
    for template in templates:
        key=cache_key(template)
        shutil.copytree(fixture/'userdata/cache'/key,data/'cache'/key)
    return data

def wait(service):
    deadline=time.monotonic()+90
    while service.task['status'] in ('running','stopping'):
        if time.monotonic()>deadline:raise TimeoutError(service.task)
        time.sleep(.002)
    if service.task['status']!='done':raise AssertionError(service.task)

spec=importlib.util.spec_from_file_location('baseline_preview_service',baseline)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
old=module.Service(prepare_data('baseline'))
current=Service(prepare_data('current'))
rows=[]
try:
    for template in templates:
        ident=template['id']
        timing={}
        before=hashlib.sha256((current.root/'cache'/cache_key(template)/'template.json').read_bytes()).hexdigest()
        for label, service, draft in (('baseline_full',old,False),('warm_full',current,False),('warm_draft',current,True)):
            measured=[]
            with patch.object(catalog,'fingerprint',wraps=catalog.fingerprint) as fingerprint:
                for i in range(5):
                    begin=time.perf_counter()
                    args=dict(ident=ident,source=str(artworks[i%2]),settings=dict(settings,scale=75+i),scene=0)
                    if service is current:args['draft']=draft
                    service.preview(**args);wait(service)
                    measured.append(round(time.perf_counter()-begin,4))
                hash_calls=fingerprint.call_count
            timing[label]=dict(first_s=measured[0],warm_median_s=round(statistics.median(measured[1:]),4),
                               trials_s=measured,full_psd_reads=hash_calls)
        preview_sets=[]
        for art in artworks:
            current.preview(ident,str(art),settings);wait(current)
            preview_sets.append([current.root/p['url'].removeprefix('/media/') for p in current.task['preview']])
        retained=current.preview_session.retained_bytes
        current.batch([ident],str(inputs),str(run/'output'),settings);wait(current)
        batch=Path(current.task['results'][-1])
        exported=[]
        for art, previews in zip(artworks,preview_sets):
            files=sorted((batch/art.stem).glob('*.png'))
            assert len(files)==len(previews)>0
            for preview, output in zip(previews,files):
                with Image.open(preview) as a, Image.open(output) as b:
                    assert a.size==b.size and a.convert('RGBA').tobytes()==b.convert('RGBA').tobytes(),(preview,output)
            exported.extend(files)
        after=hashlib.sha256((current.root/'cache'/cache_key(template)/'template.json').read_bytes()).hexdigest()
        assert before==after,'Draft rendering mutated full-resolution manifest'
        rows.append(dict(template=template['name'],timing=timing,retained_mib=round(retained/1024**2,2),
                         full_preview_export_identical=True,exported_images=len(exported),manifest_unchanged=True))
        print(json.dumps(rows[-1],ensure_ascii=True),flush=True)
    assert original_hashes=={path:catalog.fingerprint(path)['sha256'] for path in original_hashes}
    report=dict(status='PASS',run=str(run),templates=rows,source_psds_unchanged=True,
                note='Warm timings include service checks, rendering and PNG file writes. They exclude HTTP/UI polling and image display. Retained memory counts decoded renderer images/maps, not whole-process RSS.')
    (run/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: '+str(run),flush=True)
finally:
    current.close()
