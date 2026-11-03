"""Limited support for Photoshop's underlying-gray Blend If sliders.

Only the actual template's configuration is accepted: unmodified source and
individual color channels, with a composite destination (underlying gray)
range. Pillow's encoded-RGB grayscale is used; Photoshop's precise grayscale
conversion is not publicly specified, so reference-image validation is needed.
"""

from functools import lru_cache
import numbers

import numpy as np
from PIL import Image


_DEFAULT_PAIR = (0, 65535)


def _validate_sliders(sliders):
    if (len(sliders) != 4
            or any(not isinstance(value, numbers.Integral) or not 0 <= value <= 255
                   for value in sliders)):
        raise ValueError('混合颜色带必须包含 4 个 0～255 的整数滑块值。')
    if not all(a <= b for a, b in zip(sliders, sliders[1:])):
        raise ValueError('暂不支持重叠或反向的混合颜色带滑块。')
    return [int(value) for value in sliders]


def parse_blend_if(layer):
    """Return None or [black-left, black-right, white-left, white-right].

    Reject unsupported configurations rather than silently dropping their
    appearance. PSD stores each slider pair as a big-endian 16-bit integer.
    """
    record = getattr(layer, '_record', None)
    ranges = getattr(record, 'blending_ranges', None)
    if ranges is None:
        return None
    composite = ranges.composite_ranges
    channels = ranges.channel_ranges
    for channel in channels or []:
        if len(channel) != 2 or any(tuple(pair) != _DEFAULT_PAIR for pair in channel):
            raise ValueError('暂不支持单独颜色通道的混合颜色带。')
    if composite is None or len(composite) == 0:
        return None
    if len(composite) != 2 or any(len(pair) != 2 for pair in composite):
        raise ValueError('混合颜色带 composite 数据无效。')
    source, destination = map(tuple, composite)
    if source != _DEFAULT_PAIR:
        raise ValueError('暂不支持“本图层”混合颜色带；当前支持下层灰度滑块。')
    if destination == _DEFAULT_PAIR:
        return None
    if any(not isinstance(value, numbers.Integral) or not 0 <= value <= 65535
           for value in destination):
        raise ValueError('混合颜色带 destination 数据无效。')
    black, white = destination
    return _validate_sliders([black >> 8, black & 255, white >> 8, white & 255])


def _gray_gate(sliders):
    black_left, black_right, white_left, white_right = _validate_sliders(sliders)
    gray = np.arange(256, dtype=np.float32)
    if black_left == black_right:
        dark = np.ones(256, dtype=np.float32) if black_left == 0 else (gray >= black_right).astype(np.float32)
    else:
        dark = ((gray - black_left) / (black_right - black_left)).clip(0, 1)
    if white_left == white_right:
        light = np.ones(256, dtype=np.float32) if white_right == 255 else (gray <= white_left).astype(np.float32)
    else:
        light = ((white_right - gray) / (white_right - white_left)).clip(0, 1)
    return np.minimum(dark, light)


@lru_cache(maxsize=32)
def _opaque_alpha_lookup(sliders):
    # A two-dimensional byte LUT preserves rounded float-ramp precision while
    # avoiding per-pixel float buffers for the common opaque underlying plane.
    gate = _gray_gate(sliders)
    alpha = np.arange(256, dtype=np.float32)[:, None]
    lookup = np.rint(alpha * gate[None, :]).astype(np.uint8)
    lookup.setflags(write=False)
    return lookup


def apply_blend_if(backdrop, source, sliders):
    """Return source with its alpha gated by the current underlying image.

    Source RGB is retained byte for byte, and neither input is changed. The
    gray gate is interpolated toward 1 by backdrop transparency, so an isolated
    source on an empty canvas is not hidden by nonexistent underlying pixels.
    Masking and layer opacity should already be included in source alpha.
    """
    if backdrop.size != source.size:
        raise ValueError('Blend If source and backdrop must have the same size.')
    output = source.copy() if source.mode == 'RGBA' else source.convert('RGBA')
    if sliders is None:
        return output
    table = _gray_gate(sliders)
    if np.all(table == 1):
        return output
    if backdrop.mode != 'RGBA':
        backdrop = backdrop.convert('RGBA')
    backdrop_alpha = backdrop.getchannel('A')
    alpha_min, alpha_max = backdrop_alpha.getextrema()
    if alpha_max == 0:
        return output
    gray = np.asarray(backdrop.convert('L'))
    source_alpha = np.asarray(output.getchannel('A'))
    if alpha_min == 255:
        lookup = _opaque_alpha_lookup(tuple(sliders))
        output.putalpha(Image.fromarray(lookup[source_alpha, gray], 'L'))
        return output
    underlying_alpha = np.asarray(backdrop_alpha)
    adjusted_alpha = np.empty_like(source_alpha)
    # Bounded 2D working arrays avoid full-size RGBA float buffers.
    for top in range(0, output.height, 256):
        bottom = min(top + 256, output.height)
        gate = table[gray[top:bottom]]
        coverage = underlying_alpha[top:bottom].astype(np.float32) / np.float32(255)
        effective_gate = np.float32(1) - coverage * (np.float32(1) - gate)
        adjusted_alpha[top:bottom] = np.rint(source_alpha[top:bottom] * effective_gate).astype(np.uint8)
    output.putalpha(Image.fromarray(adjusted_alpha, 'L'))
    return output
