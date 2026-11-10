"""Bounded-memory, straight-alpha compositing in encoded RGB.

Masks, fill opacity and layer opacity must already be folded into source alpha.
RGB multiplication is performed in the supplied encoded colour space, matching
the engine's existing sRGB-pixel compositing convention.
"""
import numpy as np
from PIL import Image, ImageChops

SUPPORTED_MODES = frozenset(('normal', 'multiply', 'screen', 'soft_light', 'darken',
    'lighten', 'overlay', 'hard_light', 'difference', 'exclusion',
    'linear_dodge', 'linear_burn', 'color_dodge', 'color_burn'))


def composite_rgba(backdrop, source, mode='normal', chunk_rows=128):
    """Return source over backdrop with the requested encoded-RGB blend.

    Both inputs are left unchanged. Normal uses Pillow's compositor. An opaque
    multiply backdrop uses Pillow's C operations; other alpha combinations use
    the general blend-and-source-over equation in bounded NumPy row chunks.
    """
    if mode not in SUPPORTED_MODES:
        raise ValueError('Unsupported blend mode: ' + str(mode))
    if backdrop.size != source.size:
        raise ValueError('Backdrop and source dimensions differ')
    if chunk_rows < 1:
        raise ValueError('chunk_rows must be positive')
    if backdrop.mode != 'RGBA': backdrop = backdrop.convert('RGBA')
    if source.mode != 'RGBA': source = source.convert('RGBA')
    if mode == 'normal':
        return Image.alpha_composite(backdrop, source)
    source_alpha = source.getchannel('A')
    bounds = source_alpha.getbbox()
    if bounds is None:
        return backdrop.copy()
    backdrop_channel = backdrop.getchannel('A')
    backdrop_alpha = backdrop_channel.getextrema()
    if backdrop_alpha == (0, 0):
        # Blending against an absent backdrop keeps the source, rather than
        # multiplying it by the hidden RGB stored under zero backdrop alpha.
        return Image.alpha_composite(backdrop, source)
    whole = bounds == (0, 0, source.width, source.height)
    back_region = backdrop if whole else backdrop.crop(bounds)
    source_region = source if whole else source.crop(bounds)
    if mode == 'multiply' and backdrop_alpha == (255, 255):
        paint = ImageChops.multiply(back_region.convert('RGB'), source_region.convert('RGB')).convert('RGBA')
        paint.putalpha(source_alpha if whole else source_alpha.crop(bounds))
        blended = Image.alpha_composite(back_region, paint)
        if whole: return blended
        output = backdrop.copy(); output.paste(blended, bounds)
        return output

    b = np.asarray(back_region, dtype=np.uint8)
    s = np.asarray(source_region, dtype=np.uint8)
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
            if mode == 'darken':
                blended = np.minimum(cb, cs)
            elif mode == 'screen':
                blended = cb + cs - cb * cs
            elif mode == 'soft_light':
                d = np.where(cb <= 0.25, ((16.0 * cb - 12.0) * cb + 4.0) * cb, np.sqrt(cb))
                blended = np.where(cs <= 0.5, cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb),
                                   cb + (2.0 * cs - 1.0) * (d - cb))
            elif mode == 'lighten': blended = np.maximum(cb, cs)
            elif mode in ('overlay', 'hard_light'):
                condition = cb if mode == 'overlay' else cs
                blended = np.where(condition <= .5, 2*cb*cs, 1-2*(1-cb)*(1-cs))
            elif mode == 'difference': blended = np.abs(cb-cs)
            elif mode == 'exclusion': blended = cb+cs-2*cb*cs
            elif mode == 'linear_dodge': blended = np.minimum(1, cb+cs)
            elif mode == 'linear_burn': blended = np.maximum(0, cb+cs-1)
            elif mode == 'color_dodge':
                blended = np.where(cb == 0, 0, np.minimum(1, np.divide(cb, 1-cs,
                    out=np.ones_like(cb), where=cs < 1)))
            elif mode == 'color_burn':
                blended = np.where(cb == 1, 1, 1-np.minimum(1, np.divide(1-cb, cs,
                    out=np.ones_like(cb), where=cs > 0)))
            premultiplied = cb * ab * (1.0 - a) + cs * a * (1.0 - ab) + blended * a * ab
        rgb = np.divide(premultiplied, alpha, out=np.zeros_like(premultiplied), where=alpha > 0)
        result[top:bottom, :, :3] = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        result[top:bottom, :, 3] = np.rint(np.clip(alpha[:, :, 0], 0.0, 1.0) * 255.0).astype(np.uint8)
    blended = Image.fromarray(result, 'RGBA')
    if whole: return blended
    output = backdrop.copy()
    if backdrop_alpha[0] == 0:
        # The former full-frame float kernel zeroed hidden RGB at zero alpha.
        # Preserve those exact bytes, including pixels outside the active area.
        output.paste((0, 0, 0, 0), mask=backdrop_channel.point([255] + [0] * 255))
    output.paste(blended, bounds)
    return output
