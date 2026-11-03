"""8-bit modern brightness/contrast without the optional SciPy dependency.

The curve matches the bundled psd-tools modern brightness/contrast model. It
does not claim to reproduce every Photoshop color-management configuration.
Layer masks and layer opacity affect RGB only; the canvas alpha is preserved.
"""

import math
import numbers

import numpy as np
from PIL import Image


def _natural_cubic_spline(x, y, domain):
    """Evaluate a natural cubic interpolant using a small NumPy linear solve."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    domain = np.asarray(domain, dtype=np.float64)
    if x.ndim != 1 or y.shape != x.shape or len(x) < 2:
        raise ValueError('Spline knots must be equal-length one-dimensional arrays.')
    widths = np.diff(x)
    if not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(widths <= 0):
        raise ValueError('Spline knots must be finite and strictly increasing.')
    second = np.zeros_like(x)
    if len(x) > 2:
        diagonal = 2 * (widths[:-1] + widths[1:])
        matrix = np.diag(diagonal)
        if len(x) > 3:
            matrix += np.diag(widths[1:-1], 1) + np.diag(widths[1:-1], -1)
        right = 6 * np.diff(np.diff(y) / widths)
        second[1:-1] = np.linalg.solve(matrix, right)
    segment = np.clip(np.searchsorted(x, domain, side='right') - 1, 0, len(x) - 2)
    width = widths[segment]
    left = x[segment + 1] - domain
    right = domain - x[segment]
    return (second[segment] * left**3 / (6 * width)
            + second[segment + 1] * right**3 / (6 * width)
            + (y[segment] - second[segment] * width**2 / 6) * left / width
            + (y[segment + 1] - second[segment + 1] * width**2 / 6) * right / width)


def brightness_contrast_lut(layer):
    """Return 256 integer entries for a supported modern adjustment layer.

    The bundled vendor's brightness polynomial is reused, while its SciPy
    natural contrast spline is replaced with the mathematically equivalent
    NumPy solve. The normalized result is rounded once to an 8-bit LUT.
    """
    if getattr(layer, 'use_legacy', False):
        raise ValueError('暂不支持旧版亮度/对比度（useLegacy）。')
    if getattr(layer, 'lab', False):
        raise ValueError('暂不支持 Lab 亮度/对比度。')
    if getattr(layer, 'automatic', False):
        raise ValueError('暂不支持自动亮度/对比度参数。')
    if getattr(layer, 'vrsn', 1) != 1:
        raise ValueError('暂不支持此版本的亮度/对比度参数。')
    if getattr(layer, 'mean', 127) != 127:
        raise ValueError('暂不支持非标准 mean 参数的亮度/对比度。')
    psd = getattr(layer, '_psd', None)
    if psd is not None:
        from psd_tools.constants import ColorMode
        if psd.depth != 8 or psd.color_mode not in (ColorMode.RGB, ColorMode.GRAYSCALE):
            raise ValueError('亮度/对比度目前只支持 8 位 RGB 或灰度 PSD。')
    try:
        brightness = float(layer.brightness)
        contrast = float(layer.contrast)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('亮度/对比度参数无效。') from exc
    if (not math.isfinite(brightness) or not math.isfinite(contrast)
            or not -150 <= brightness <= 150 or not -100 <= contrast <= 100):
        raise ValueError('亮度/对比度参数超出当前支持范围。')

    # This import does not require SciPy. Only the vendor adjustment entry point
    # imports SciPy; the reusable polynomial and constants are pure NumPy.
    from psd_tools.composite.adjustments import (
        _CONTRAST_CONTROL_X,
        _CONTRAST_INNER_DELTA,
        _get_brightness_spline,
        _lut_domain,
    )
    zero, one = np.float32(0), np.float32(1)
    b = np.float32(brightness) / np.float32(150)
    c = np.float32(contrast) / np.float32(100)
    t = _lut_domain(256)
    x = _CONTRAST_CONTROL_X
    y = np.array([zero, x[1] - c * _CONTRAST_INNER_DELTA,
                  x[2] + c * _CONTRAST_INNER_DELTA, one], dtype=np.float32)
    contrast_spline = _natural_cubic_spline(x, y, t).astype(np.float32)
    brightness_spline = _get_brightness_spline(brightness_value=b, t=t)
    lut = np.interp(t, t - brightness_spline, contrast_spline + brightness_spline)
    lut = lut.astype(np.float32).clip(zero, one)
    return np.rint(lut * np.float32(255)).astype(np.uint8).tolist()


def apply_lut_rgba(canvas, lut, mask=None, opacity=255):
    """Apply an RGB LUT through an optional 8-bit mask, retaining alpha exactly.

    ``mask`` is a same-size Pillow image; non-L masks are converted to L.
    Opacity is an integer in [0,255]. Multiplying opacity into an 8-bit mask
    introduces at most one RGB level of quantization relative to float math.
    The input images are never modified.
    """
    if len(lut) != 256 or any(not isinstance(v, numbers.Integral) or not 0 <= v <= 255 for v in lut):
        raise ValueError('LUT must contain exactly 256 integers in [0,255].')
    if not isinstance(opacity, numbers.Integral) or not 0 <= opacity <= 255:
        raise ValueError('Opacity must be an integer in [0,255].')
    if mask is not None and mask.size != canvas.size:
        raise ValueError('Adjustment mask must have the same size as the canvas.')
    canvas = canvas.convert('RGBA')
    if opacity == 0:
        return canvas.copy()
    rgb = canvas.convert('RGB')
    adjusted = rgb.point(list(lut) * 3)
    if mask is None and opacity == 255:
        result = adjusted
    else:
        if mask is None:
            coverage = Image.new('L', canvas.size, int(opacity))
        else:
            coverage = mask.convert('L')
            if opacity != 255:
                coverage = coverage.point([round(v * int(opacity) / 255) for v in range(256)])
        result = Image.composite(adjusted, rgb, coverage)
    result.putalpha(canvas.getchannel('A'))
    return result
