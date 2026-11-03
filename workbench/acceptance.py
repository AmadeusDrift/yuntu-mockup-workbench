"""Read-only source acceptance: build/render each imported real PSD and compare fingerprints."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap
import catalog
import engine
from service import Service
from PIL import Image, ImageDraw, ImageOps

app = Service(bootstrap.ROOT / 'data')
destination = app.root / 'acceptance'
destination.mkdir(exist_ok=True)
artwork = destination / 'test-artwork.png'
image = Image.new('RGBA', (700, 700))
draw = ImageDraw.Draw(image)
for y in range(0, 700, 100):
    for x in range(0, 700, 100):
        draw.rectangle((x, y, x + 99, y + 99), fill=(28, 150, 220, 255) if (x + y)//100 % 2 else (225, 45, 90, 255))
image.save(artwork)
settings = engine.settings_checked(dict(width=0, height=0, ppi=72, quality=100, fit='auto', format='jpg', trim=False))
results = []
for index, template in enumerate(app.templates):
    started = time.perf_counter()
    before = catalog.fingerprint(template['path'])
    result = {'name': template['name']}
    try:
        manifest = app.manifest(template)
        built = time.perf_counter()
        with app.cache.use(manifest):
            renderer = engine.Renderer(manifest)
            sheet = Image.new('RGB', (1040, 580), 'white')
            count = 0
            for count, (name, image) in enumerate(renderer.render(artwork, settings), 1):
                tile = Image.new('RGB', (260, 290), 'white')
                thumb = ImageOps.contain(image, (250, 250))
                tile.paste(thumb, ((260 - thumb.width)//2, 0))
                ImageDraw.Draw(tile).text((8, 265), 'Scene ' + name, fill='black')
                sheet.paste(tile, (((count - 1)%4)*260, ((count - 1)//4)*290))
                image.close()
            output = destination / f'template-{index + 1:02d}.jpg'
            sheet.save(output, quality=90)
            del renderer
        assert count == sum(s['mode'] != 'skip' for s in template['scenes'])
        assert before == catalog.fingerprint(template['path']), 'source PSD changed'
        result.update(status='PASS', scenes=count, build_seconds=round(built-started, 3),
                      total_seconds=round(time.perf_counter()-started, 3), preview=str(output), source_unchanged=True)
    except Exception as exc:
        result.update(status='FAIL', error=str(exc))
    results.append(result)
    print(json.dumps(result, ensure_ascii=True), flush=True)
(destination / 'report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'passed':sum(r['status']=='PASS' for r in results),'total':len(results)}), flush=True)
raise SystemExit(any(r['status'] != 'PASS' for r in results))
