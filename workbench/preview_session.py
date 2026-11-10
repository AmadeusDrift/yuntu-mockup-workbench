"""One bounded decoded template shared by sequential interactive preview jobs."""
from contextlib import contextmanager
from pathlib import Path

import engine


def file_identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def renderer_images(renderer):
    seen = set()
    images = list(renderer.assets.values()) + list(renderer._fixed_images.values())
    images.extend(image for pair in renderer._static_layers.values() for image in pair if image is not None)
    for image in images:
        if id(image) not in seen:
            seen.add(id(image))
            yield image


def memory_bytes(renderer):
    if renderer is None:
        return 0
    size = sum(image.width * image.height * len(image.getbands()) for image in renderer_images(renderer))
    return size + sum(array.nbytes for mapping in renderer.warp_maps.values()
                      for array in mapping.values() if hasattr(array, 'nbytes'))


def close_renderer(renderer):
    if renderer is not None:
        for image in renderer_images(renderer):
            image.close()
        renderer.assets.clear()
        renderer.warp_maps.clear()
        renderer._static_layers.clear()
        renderer._fixed_images.clear()


class PreviewSession:
    """Owns resources only between jobs; callers serialize jobs in Service.start."""
    def __init__(self, cache, limit_bytes=512 * 1024**2):
        self.cache = cache
        self.limit_bytes = limit_bytes
        self.key = None
        self.renderer = None
        self.draft = None
        self.pin = None

    @property
    def retained_bytes(self):
        return memory_bytes(self.renderer) + memory_bytes(self.draft)

    def close(self):
        close_renderer(self.draft)
        close_renderer(self.renderer)
        self.renderer = self.draft = self.key = None
        pin, self.pin = self.pin, None
        if pin is not None:
            pin.__exit__(None, None, None)

    @contextmanager
    def use(self, manifest, draft=False):
        key = file_identity(manifest)
        if self.key != key:
            self.close()
            self.pin = self.cache.use(manifest)
            self.pin.__enter__()
            try:
                self.renderer = engine.Renderer(manifest)
                self.key = key
            except BaseException:
                self.close()
                raise
        try:
            if draft and self.draft is None:
                self.draft = self.renderer.scaled_preview(720)
            yield self.draft if draft else self.renderer
        except BaseException:
            self.close()
            raise
        finally:
            if self.retained_bytes > self.limit_bytes:
                self.close()
