"""Normal, multiply, screen and soft-light straight-alpha compositing.

Masks, fill opacity and layer opacity must already be folded into source alpha.
RGB multiplication is performed in the supplied encoded colour space, matching
the engine's existing sRGB-pixel compositing convention.
"""
import numpy as np
from PIL import Image, ImageChops


def composite_rgba(backdrop, source, mode='normal', chunk_rows=128):
    """Return source over backdrop with the requested encoded-RGB blend.

    Both inputs are left unchanged. Normal uses Pillow's compositor. An opaque
    multiply backdrop uses Pillow's C operations; other alpha combinations use
    the general blend-and-source-over equation in bounded NumPy row chunks.
    """
    if mode not in ('normal', 'multiply', 'screen', 'soft_light'):
        raise ValueError('Unsupported blend mode: ' + str(mode))
    if backdrop.size != source.size:
        raise ValueError('Backdrop and source dimensions differ')
    if chunk_rows < 1:
        raise ValueError('chunk_rows must be positive')
    backdrop = backdrop.convert('RGBA')
    source = source.convert('RGBA')
    if mode == 'normal':
        return Image.alpha_composite(backdrop, source)
    source_alpha = source.getchannel('A')
    if source_alpha.getextrema() == (0, 0):
        return backdrop.copy()
    backdrop_alpha = backdrop.getchannel('A').getextrema()
    if backdrop_alpha == (0, 0):
        # Blending against an absent backdrop keeps the source, rather than
        # multiplying it by the hidden RGB stored under zero backdrop alpha.
        return Image.alpha_composite(backdrop, source)
    if mode == 'multiply' and backdrop_alpha == (255, 255):
        paint = ImageChops.multiply(backdrop.convert('RGB'), source.convert('RGB')).convert('RGBA')
        paint.putalpha(source_alpha)
        return Image.alpha_composite(backdrop, paint)

    b = np.asarray(backdrop, dtype=np.uint8)
    s = np.asarray(source, dtype=np.uint8)
    height, width = b.shape[:2]
    result = np.empty((height, width, 4), dtype=np.uint8)
    for top in range(0, height, chunk_rows):
        bottom = min(height, top + chunk_rows)
        bc = b[top:bottom].astype(np.float32) / 255.0
        sc = s[top:bottom].astype(np.float32) / 255.0
        cb, ab = bc[:, :, :3], bc[:, :, 3:4]
        cs, a = sc[:, :, :3], sc[:, :, 3:4]
        alpha = a + ab * (1.0 - a)
        if mode == 'multiply':
            # Keep the exact evaluation order and rounding of v1-v4 caches.
            premultiplied = cb * ab * (1.0 - a) + cs * a * ((1.0 - ab) + cb * ab)
        else:
            if mode == 'screen':
                blended = cb + cs - cb * cs
            else:
                d = np.where(cb <= 0.25, ((16.0 * cb - 12.0) * cb + 4.0) * cb, np.sqrt(cb))
                blended = np.where(cs <= 0.5, cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb),
                                   cb + (2.0 * cs - 1.0) * (d - cb))
            premultiplied = cb * ab * (1.0 - a) + cs * a * (1.0 - ab) + blended * a * ab
        rgb = np.divide(premultiplied, alpha, out=np.zeros_like(premultiplied), where=alpha > 0)
        result[top:bottom, :, :3] = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        result[top:bottom, :, 3] = np.rint(np.clip(alpha[:, :, 0], 0.0, 1.0) * 255.0).astype(np.uint8)
    return Image.fromarray(result, 'RGBA')
