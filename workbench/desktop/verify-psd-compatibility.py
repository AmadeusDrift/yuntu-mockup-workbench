"""Read-only real-PSD acceptance; optional native Photoshop PNG references."""
import argparse
import hashlib
import json
import statistics
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'workbench'))
import bootstrap, catalog, engine
from service import Service, DEFAULTS
from PIL import Image, ImageDraw
import numpy as np
from psd_tools import PSDImage

parser=argparse.ArgumentParser();parser.add_argument('psd',type=Path);parser.add_argument('--reference-dir',type=Path)
args=parser.parse_args()
run=root/'.analysis-temp'/('psd-compatibility-'+time.strftime('%Y%m%d-%H%M%S'));run.mkdir()
tempfile.tempdir=str(run)
source=run/'input';source.mkdir();output=run/'output'
for name,size,color in [('A',(480,640),(225,45,80,255)),('B',(720,480),(20,115,210,255))]:
    image=Image.new('RGBA',size);draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((20,30,size[0]-20,size[1]-30),radius=60,fill=color)
    draw.rectangle((size[0]//3,40,size[0]*2//3,size[1]-40),fill=(250,235,75,255))
    image.save(source/(name+'.png'));image.close()
before=catalog.fingerprint(args.psd)['sha256']
service=Service(run/'data')
def wait():
    deadline=time.monotonic()+240
    while service.task['status'] in ('running','paused','stopping'):
        if time.monotonic()>deadline:raise TimeoutError(service.task['message'])
        time.sleep(.01)
    assert service.task['status']=='done',service.task['error']
start=time.perf_counter();service.scan([str(args.psd)]);wait();import_seconds=time.perf_counter()-start
template=service.templates[0];assert template['compatibility']['status']!='blocked',template['compatibility']
settings=dict(DEFAULTS['settings'],format='png',orientation='original',scale=75,offset_x=12,offset_y=-8,brightness=18,
              naming=dict(folder='{name}',image='{scene:02}',start=1),input_layout='flat',input_exclude=[])
preview={}
for path in sorted(source.iterdir()):
    service.preview(template['id'],str(path),settings);wait()
    expected=run/('expected-'+path.stem);expected.mkdir()
    preview[path.stem]=[]
    for index,entry in enumerate(service.task['preview']):
        copy=expected/f'{index}.png'
        shutil.copyfile(run/'data'/entry['url'].removeprefix('/media/'),copy)
        preview[path.stem].append(copy)
manifest=service.manifest(template);data=json.loads(manifest.read_text(encoding='utf-8'))
manifest_hash=hashlib.sha256(manifest.read_bytes()).hexdigest()
def adjustments(ops):
    return sum((op['kind']=='adjustment')+adjustments(op.get('ops',[]))+adjustments([op['base_op']] if op.get('base_op') else []) for op in ops)
timing={}
with patch.object(catalog,'fingerprint',wraps=catalog.fingerprint) as hashed,patch.object(PSDImage,'open',wraps=PSDImage.open) as opened:
    for draft in (False,True):
        durations=[]
        for index in range(5):
            start=time.perf_counter()
            service.preview(template['id'],str(source/('A.png' if index%2 else 'B.png')),dict(settings,scale=75+index),scene=0,draft=draft);wait()
            durations.append(time.perf_counter()-start)
        timing['draft' if draft else 'full']=dict(first=durations[0],warm_median=statistics.median(durations[1:]))
    assert hashed.call_count==0 and opened.call_count==0,(hashed.call_count,opened.call_count)
service.batch([template['id']],str(source),str(output),settings);wait()
destination=Path(service.history[0]['path']);pairs=[]
for name,paths in preview.items():
    for index,path in enumerate(paths,1):
        export=destination/name/f'{index:02}.png'
        with Image.open(path) as a,Image.open(export) as b:
            assert a.convert('RGBA').tobytes()==b.convert('RGBA').tobytes(),export
        pairs.append(f'{name}/{index:02}.png')
comparisons=[]
if args.reference_dir:
    psd=PSDImage.open(args.psd);nodes=catalog.scene_nodes(psd)
    for sid,group in nodes:
        name=group.name if sid!='document' else '1'
        image=catalog.scene_image(psd,group,nodes);image.save(run/f'engine-original-{name}.png')
        with Image.open(args.reference_dir/f'photoshop-scene-{name}.png') as reference:
            difference=np.abs(np.asarray(image.convert('RGB')).astype(float)-np.asarray(reference.convert('RGB')).astype(float))
            comparisons.append(dict(scene=name,mae=float(difference.mean()),p99=float(np.quantile(difference,.99)),maximum=float(difference.max())))
        image.close()
assert before==catalog.fingerprint(args.psd)['sha256'],'Source PSD changed'
assert manifest_hash==hashlib.sha256(manifest.read_bytes()).hexdigest(),'Cache manifest changed'
report=dict(status='PASS',version=json.loads((root/'workbench/desktop/package.json').read_text(encoding='utf-8'))['version'],
            source_sha256=before,source_unchanged=True,scenes=len(data['scenes']),slots=[s['slots'] for s in data['scenes']],
            import_seconds=import_seconds,compatibility=template['compatibility'],
            live_adjustment_ops=[adjustments(s['ops']) for s in data['scenes']],
            exact_preview_export_pairs=pairs,timing=timing,warm_psd_reads=0,warm_full_hashes=0,
            photoshop=comparisons)
service.close();(run/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status='PASS',run=str(run),scenes=report['scenes'],pairs=len(pairs),timing=timing,photoshop=comparisons),ensure_ascii=True))
