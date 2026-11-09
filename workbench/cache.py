"""Content-addressed disk caches; only active templates consume renderer memory."""
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = 3


def cache_key(template):
    identity = {'schema': SCHEMA, 'digest': template['digest'], 'scenes': [
        {key: s.get(key) for key in ('id', 'mode', 'targets', 'artwork_roles')} for s in template['scenes']]}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class DiskCache:
    def __init__(self, root, limit_bytes=10 * 1024**3):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit_bytes = int(limit_bytes)
        self.active = set()
        self.lock = threading.RLock()

    def _remove(self, folder):
        folder = Path(folder)
        if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
            raise ValueError('缓存目录不能是链接')
        resolved = folder.resolve()
        if resolved.parent != self.root or not re.fullmatch(r'(?:[a-f0-9]{64}|\.building_[a-f0-9]{32})', folder.name):
            raise ValueError('缓存清理范围无效')
        shutil.rmtree(resolved)

    def summary(self):
        entries = []
        # Snapshot listing must remain responsive while a builder holds the mutation lock.
        active = self.active.copy()
        for folder in self.root.iterdir():
            if not re.fullmatch('[a-f0-9]{64}', folder.name) or folder.is_symlink():
                continue
            try:
                meta = json.loads((folder / 'cache-meta.json').read_text(encoding='utf-8'))
                entries.append(dict(key=folder.name, name=meta['name'], bytes=sum(
                    p.stat().st_size for p in folder.iterdir() if p.is_file()),
                    last_used=meta['last_used'], active=folder.name in active))
            except (OSError, ValueError, KeyError):
                continue
        return {'bytes': sum(e['bytes'] for e in entries), 'limit_bytes': self.limit_bytes,
                'entries': sorted(entries, key=lambda e: e['last_used'], reverse=True)}

    def clean(self, all_unused=True):
        with self.lock:
            summary = self.summary()
            total = summary['bytes']
            for entry in sorted(summary['entries'], key=lambda e: e['last_used']):
                if entry['active']:
                    continue
                if not all_unused and total <= self.limit_bytes:
                    break
                self._remove(self.root / entry['key'])
                total -= entry['bytes']
        return self.summary()

    def ensure(self, template, builder):
        key = cache_key(template)
        with self.lock:
            folder = self.root / key
            manifest = folder / 'template.json'
            if manifest.is_file():
                meta = {'name': template['name'], 'last_used': time.time()}
                (folder / 'cache-meta.json').write_text(json.dumps(meta), encoding='utf-8')
                return manifest
            stage = self.root / ('.building_' + uuid.uuid4().hex)
            stage.mkdir()
            try:
                builder(stage)
                if not (stage / 'template.json').is_file():
                    raise ValueError('模板缓存没有完整生成')
                size = sum(p.stat().st_size for p in stage.iterdir() if p.is_file())
                if size > self.limit_bytes:
                    raise ValueError('这个模板超过缓存容量，请在缓存管理中提高容量')
                (stage / 'cache-meta.json').write_text(json.dumps(
                    {'name': template['name'], 'last_used': time.time()}), encoding='utf-8')
                stage.rename(folder)
                # Protect the just-built cache while evicting older entries.
                self.active.add(key)
                try:
                    self.clean(all_unused=False)
                finally:
                    self.active.discard(key)
                return manifest
            finally:
                if stage.exists():
                    self._remove(stage)

    @contextmanager
    def use(self, manifest):
        key = Path(manifest).parent.name
        with self.lock:
            self.active.add(key)
        try:
            yield
        finally:
            with self.lock:
                self.active.discard(key)
                self.clean(all_unused=False)
