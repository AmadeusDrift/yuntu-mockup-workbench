"""Serializable RGB8 PSD adjustments using only Pillow and NumPy.

Levels, curves, exposure and Hue/Saturation follow the bundled psd-tools model.
Exposure uses its documented 2.2 RGB profile approximation. Classic gradients
use midpoint-remapped, segment-clamped Hermite interpolation. Dither perturbs
the input tone by up to one RGB8 level using a deterministic pixel pattern;
the noise pattern does not match Photoshop's application-specific seed.
This is not a color-managed replacement for Photoshop's entire compositor.
"""
import math
import numbers

import numpy as np
from PIL import Image

from tone_adjustments import _natural_cubic_spline


SUPPORTED_KINDS = {
    'gradientmap', 'levels', 'curves', 'huesaturation',
    'exposure', 'invert', 'posterize', 'threshold',
}
_CHANNEL_KINDS = {'levels', 'curves', 'exposure', 'invert', 'posterize'}
_DOMAIN = np.arange(256, dtype=np.float64)
_LUMA = np.array([.30, .59, .11], dtype=np.float64)


def _number(value, label, low, high, integral=False):
    if (isinstance(value, bool) or not isinstance(value, numbers.Real)
            or not math.isfinite(value) or not low <= value <= high
            or (integral and not isinstance(value, numbers.Integral))):
        raise ValueError(f'{label} 参数无效或超出支持范围 [{low}, {high}]。')
    return int(value) if integral else float(value)


def _flag(value, label):
    if value not in (0, 1, False, True):
        raise ValueError(f'{label} 标记无效。')
    return bool(value)


def _version(data, allowed, label, default=None):
    value = getattr(data, 'version', default)
    if value not in allowed:
        raise ValueError(f'暂不支持此版本的{label}参数：{value}。')


def _sequence(value, label, length=None):
    if not isinstance(value, (list, tuple)) or (length is not None and len(value) != length):
        raise ValueError(f'{label} 数据格式无效。')
    return value


def _channel_spec(kind, tables):
    """Compose individual channels first, then the RGB master in 8-bit bins."""
    master = tables.get(0)
    channels = []
    for channel in (1, 2, 3):
        values = tables.get(channel, _DOMAIN)
        if master is not None:
            indices = np.floor(np.clip(values, 0, 255) + 1e-7).astype(np.uint8)
            values = master[indices]
        channels.append(np.rint(values).clip(0, 255).astype(np.uint8).tolist())
    return {'type': kind, 'luts': channels}


def _compile_levels(layer):
    data = getattr(layer, 'data', None)
    if data is None:
        raise ValueError('色阶记录缺失。')
    _version(data, (2,), '色阶', default=2)
    if getattr(data, 'extra_version', None) not in (None, 3):
        raise ValueError('暂不支持此色阶扩展版本。')
    records = list(data)
    if not records:
        raise ValueError('色阶记录为空。')
    tables = {}
    for channel, record in enumerate(records):
        fields = [getattr(record, field, None) for field in
                  ('input_floor', 'input_ceiling', 'output_floor', 'output_ceiling', 'gamma')]
        if channel > 3:
            # PSD always stores 29 records; absent channels may be zero-filled.
            if fields not in ([0, 255, 0, 255, 100], [0, 0, 0, 0, 0]):
                raise ValueError('暂不支持 RGB 主通道之外的非默认色阶记录。')
            continue
        black, white, out_black, out_white = [
            _number(value, '色阶端点', 0, 255, True) for value in fields[:4]]
        gamma = _number(fields[4], '色阶 Gamma', 1, 999, True) / 100
        if white <= black:
            raise ValueError('色阶输入白场必须大于黑场。')
        values = np.clip((_DOMAIN - black) / (white-black), 0, 1) ** (1/gamma)
        tables[channel] = values * (out_white-out_black) + out_black
    return _channel_spec('levels', tables)


def _compile_curves(layer):
    data = getattr(layer, 'data', None)
    if data is None:
        raise ValueError('曲线记录缺失。')
    _version(data, (1, 4), '曲线')
    is_map = _flag(getattr(data, 'is_map', None), '曲线映射')
    extra = getattr(layer, 'extra', getattr(data, 'extra', None))
    if extra is not None:
        _version(extra, (3, 4), '曲线扩展', default=4)
        records = [(getattr(item, 'channel_id', None), getattr(item, 'points', None)) for item in extra]
    else:
        raw = _sequence(getattr(data, 'data', None), '曲线')
        count = _number(getattr(data, 'count_map', None), '曲线通道', 1, 2**32-1, True)
        channels = [i for i in range(32) if count & (1 << i)] if data.version == 1 else list(range(count))
        if len(channels) != len(raw):
            raise ValueError('曲线通道数量与记录数量不符。')
        records = list(zip(channels, raw))
    if not records:
        raise ValueError('曲线记录为空。')
    tables = {}
    for channel, points in records:
        channel = _number(channel, '曲线通道', 0, 3, True)
        if channel in tables:
            raise ValueError('曲线通道记录重复。')
        points = _sequence(points, '曲线点')
        if is_map:
            if len(points) != 256:
                raise ValueError('曲线映射必须包含 256 个值。')
            values = np.array([_number(v, '曲线映射值', 0, 255, True) for v in points])
        else:
            if not 2 <= len(points) <= 19:
                raise ValueError('曲线必须包含 2 至 19 个控制点。')
            checked = [[_number(v, '曲线控制点', 0, 255, True) for v in _sequence(p, '曲线控制点', 2)] for p in points]
            y, x = np.array(checked, dtype=np.float64).T
            if np.any(np.diff(x) <= 0):
                raise ValueError('曲线输入控制点必须严格递增。')
            values = _natural_cubic_spline(x, y, np.clip(_DOMAIN, x[0], x[-1])).clip(0, 255)
        tables[channel] = values
    return _channel_spec('curves', tables)


def _compile_exposure(layer):
    raw = getattr(layer, '_data', None)
    if raw is not None:
        _version(raw, (1,), '曝光度')
    exposure = _number(getattr(layer, 'exposure', None), '曝光度', -20, 20)
    offset = _number(getattr(layer, 'exposure_offset', None), '曝光度位移', -.5, .5)
    gamma = _number(getattr(layer, 'gamma', None), '曝光度 Gamma', .01, 9.99)
    values = ((_DOMAIN / 255) ** 2.2 * 2**exposure + offset).clip(0, 1)
    values = values ** (1/gamma/2.2) * 255
    return _channel_spec('exposure', {0: values})


def _hsl_tuple(value, colorize=False):
    hue, saturation, lightness = _sequence(value, '色相/饱和度', 3)
    return [_number(hue, '色相', 0 if colorize else -180, 360 if colorize else 180),
            _number(saturation, '饱和度', 0 if colorize else -100, 100),
            _number(lightness, '明度', -100, 100)]


def _validate_hsl(spec):
    colorize = _flag(spec.get('colorize'), '着色')
    master = _hsl_tuple(spec.get('master'))
    colorization = _hsl_tuple(spec.get('colorization'), True)
    ranges = []
    for item in _sequence(spec.get('ranges'), '色相范围'):
        limits, settings = _sequence(item, '色相范围记录', 2)
        limits = [_number(v, '色相范围端点', -360, 360) for v in _sequence(limits, '色相范围端点', 4)]
        settings = _hsl_tuple(settings)
        offsets = [(v-limits[0]) % 360 for v in limits]
        if offsets != sorted(offsets) or offsets[-1] == 0:
            raise ValueError('色相范围必须按环形顺序排列且跨度大于零。')
        ranges.append([limits, settings])
    if len(ranges) > 6:
        raise ValueError('色相/饱和度最多支持六个选定颜色范围。')
    return colorize, master, colorization, ranges


def _compile_hsl(layer):
    raw = getattr(layer, '_data', None)
    if raw is not None:
        _version(raw, (2,), '色相/饱和度')
    spec = {'type': 'huesaturation', 'colorize': getattr(layer, 'enable_colorization', None),
            'master': getattr(layer, 'master', None), 'colorization': getattr(layer, 'colorization', None),
            'ranges': getattr(layer, 'data', None)}
    colorize, master, colorization, ranges = _validate_hsl(spec)
    return {'type': 'huesaturation', 'colorize': colorize, 'master': master,
            'colorization': colorization, 'ranges': ranges}


def _compile_gradient(layer):
    data = getattr(layer, '_data', None)
    if data is None:
        raise ValueError('渐变映射记录缺失。')
    _version(data, (1, 3), '渐变映射')
    method = getattr(data, 'method', b'Gcls')
    method = getattr(method, 'value', method)
    if method != b'Gcls':
        raise ValueError('渐变映射目前只支持经典（Classic）插值。')
    if getattr(data, 'mode', None) != 0:
        raise ValueError('渐变映射目前只支持实色渐变，不支持杂色渐变。')
    if getattr(data, 'expansion', None) != 2 or getattr(data, 'length', None) != 32:
        raise ValueError('暂不支持此渐变映射参数格式。')
    reverse = _flag(getattr(data, 'is_reversed', None), '渐变反向')
    dither = _flag(getattr(data, 'is_dithered', None), '渐变仿色')
    smoothness = _number(getattr(data, 'interpolation', None), '渐变平滑度', 0, 4096, True) / 4096
    stops = _sequence(getattr(data, 'color_stops', None), '渐变颜色色标')
    if not 2 <= len(stops) <= 4096:
        raise ValueError('渐变映射必须包含至少两个颜色色标。')
    positions, colors, midpoints = [], [], []
    for stop in stops:
        if getattr(stop, 'mode', None) != 0:
            raise ValueError('渐变映射目前只支持 RGB 颜色色标。')
        positions.append(_number(getattr(stop, 'location', None), '渐变色标位置', 0, 4096, True))
        midpoints.append(_number(getattr(stop, 'midpoint', None), '渐变中点', 1, 99, True) / 100)
        components = _sequence(getattr(stop, 'color', None), '渐变色标颜色', 4)
        colors.append([_number(c, '渐变色标颜色', 0, 65535, True) / 257 for c in components[:3]])
    positions = np.array(positions, dtype=np.float64)
    if np.any(np.diff(positions) <= 0):
        raise ValueError('渐变颜色色标必须严格递增。')
    transparency = _sequence(getattr(data, 'transparency_stops', None), '渐变透明度色标')
    if not transparency:
        raise ValueError('渐变透明度记录缺失。')
    for stop in transparency:
        _number(getattr(stop, 'location', None), '渐变透明度位置', 0, 4096, True)
        _number(getattr(stop, 'midpoint', None), '渐变透明度中点', 1, 99, True)
        if getattr(stop, 'opacity', None) != 255:
            raise ValueError('渐变映射目前只支持完全不透明的渐变色标。')

    # Classic channel interpolation: piecewise midpoint remapping followed by
    # Hermite curves, blending tangents toward the chord for lower smoothness.
    # Primary derivation: https://pastila.org/essays/classic-gradient-interpolation/
    colors = np.array(colors, dtype=np.float64)
    chords = np.diff(colors, axis=0)
    tangents = (np.vstack([np.zeros(3), chords]) + np.vstack([chords, np.zeros(3)])) / 2
    domain = np.linspace(0, 4096, 4097)
    if reverse:
        domain = 4096-domain
    domain = np.clip(domain, positions[0], positions[-1])
    segment = np.clip(np.searchsorted(positions, domain, side='right')-1, 0, len(positions)-2)
    u = (domain-positions[segment]) / (positions[segment+1]-positions[segment])
    midpoint = np.array(midpoints)[segment]
    t = np.where(u <= midpoint, u/(2*midpoint), .5+(u-midpoint)/(2*(1-midpoint)))[:, None]
    first, last = colors[segment], colors[segment+1]
    chord = last-first
    start_tangent = (1-smoothness)*chord + smoothness*tangents[segment]
    end_tangent = (1-smoothness)*chord + smoothness*tangents[segment+1]
    values = ((2*t**3-3*t**2+1)*first + (t**3-2*t**2+t)*start_tangent
              + (-2*t**3+3*t**2)*last + (t**3-t**2)*end_tangent)
    values = np.clip(values, np.minimum(first, last), np.maximum(first, last))
    return {'type': 'gradientmap', 'lut': np.round(values, 7).tolist(), 'dithered': dither}


def compile_adjustment(layer):
    """Compile a supported RGB8 PSD layer into a detached JSON-safe spec.

    Reject unsupported formats rather than accepting an identity substitute.
    Caller applies the PSD layer's masks, opacity and clipping separately.
    """
    kind = getattr(layer, 'kind', None)
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f'暂不支持此类调整图层：{kind}。')
    psd = getattr(layer, '_psd', None)
    if psd is not None:
        from psd_tools.constants import ColorMode
        if psd.depth != 8 or psd.color_mode != ColorMode.RGB:
            raise ValueError('此调整目前只支持 8 位 RGB PSD。')
    try:
        if kind == 'levels':
            return _compile_levels(layer)
        if kind == 'curves':
            return _compile_curves(layer)
        if kind == 'exposure':
            return _compile_exposure(layer)
        if kind == 'huesaturation':
            return _compile_hsl(layer)
        if kind == 'gradientmap':
            return _compile_gradient(layer)
        if kind == 'invert':
            return _channel_spec(kind, {0: 255-_DOMAIN})
        if kind == 'posterize':
            count = _number(getattr(layer, 'posterize', None), '色调分离级数', 2, 255, True)
            return _channel_spec(kind, {0: np.floor(_DOMAIN*count/256) * 255/(count-1)})
        threshold = _number(getattr(layer, 'threshold', None), '阈值', 1, 255, True)
        return {'type': kind, 'threshold': threshold}
    except (AttributeError, TypeError, IndexError, KeyError) as exc:
        raise ValueError(f'{kind} 调整记录格式无效。') from exc


def _saturation_mix(left, right):
    """Bilinear equivalent of the vendor's SciPy regular-grid interpolator."""
    from psd_tools.composite.adjustments import _SATURATION_RANGE_INTERPOLATION_GRID
    grid = _SATURATION_RANGE_INTERPOLATION_GRID
    x, y = (np.clip(left, -1, 1)+1)*10, (np.clip(right, -1, 1)+1)*10
    ix, iy = np.minimum(np.floor(x).astype(int), 19), np.minimum(np.floor(y).astype(int), 19)
    fx, fy = x-ix, y-iy
    return ((1-fx)*((1-fy)*grid[ix, iy] + fy*grid[ix, iy+1])
            + fx*((1-fy)*grid[ix+1, iy] + fy*grid[ix+1, iy+1])).astype(np.float32)


def _apply_hsl(rgb, settings):
    from psd_tools.composite.adjustments import (
        _apply_lightness, _apply_saturation, _correct_saturation,
        _get_huesaturation_range_mask, _huesaturation_colorize,
    )
    from psd_tools.composite.blend import rgb2hsl, hsl2rgb, hsl2hsv, hsv2hsl
    colorize, master, colorization, ranges = settings
    img = rgb.astype(np.float32) / np.float32(255)
    normalization = np.array([360, 100, 100], dtype=np.float32)
    if colorize:
        return _huesaturation_colorize(img, tuple(np.array(colorization, dtype=np.float32)/normalization)) * 255
    active = [(limits, settings) for limits, settings in ranges if any(settings)]
    if not any(master) and not active:
        return rgb
    hue, saturation, lightness = np.array(master, dtype=np.float32) / normalization
    hsl = rgb2hsl(_apply_lightness(img, lightness))
    range_hue = np.zeros_like(hsl[..., 0:1])
    range_saturation = np.zeros_like(range_hue)
    range_lightness = np.zeros_like(range_hue)
    for limits, adjustment in active:
        h, s, l = np.array(adjustment, dtype=np.float32) / normalization
        mask = _get_huesaturation_range_mask(hsl[..., 0:1], tuple(limits))
        range_hue += h*mask
        range_lightness = np.clip(range_lightness+l*mask, -1, 1)
        contribution = s * (1-(1-mask)**(1.5/(1-s**4+.05))) if s > 0 else s*mask
        range_saturation = _saturation_mix(range_saturation, contribution)
    hsl = hsl2hsv(hsl)
    sv, value = hsl[..., 1:2], hsl[..., 2:3]
    hsl[..., 2:3] = np.where(range_lightness >= 0, value, value*(1+range_lightness*sv)).clip(0, 1)
    hsl[..., 1:2] = _correct_saturation(range_lightness, sv)
    hsl = hsv2hsl(hsl)
    hsl[..., 0:1] = (hsl[..., 0:1]+range_hue+hue) % 1
    hsl[..., 1:2] = _apply_saturation(_apply_saturation(hsl[..., 1:2], saturation), range_saturation)
    return hsl2rgb(hsl) * 255


def _validated_spec(spec):
    if not isinstance(spec, dict) or spec.get('type') not in SUPPORTED_KINDS:
        raise ValueError('调整操作类型无效。')
    kind = spec['type']
    if kind in _CHANNEL_KINDS:
        tables = spec.get('luts')
        if not isinstance(tables, list) or len(tables) != 3:
            raise ValueError('调整 LUT 必须包含三个通道。')
        for table in tables:
            if not isinstance(table, list) or len(table) != 256:
                raise ValueError('调整 LUT 必须包含 256 个值。')
            if any(not isinstance(v, numbers.Integral) or isinstance(v, bool) or not 0 <= v <= 255 for v in table):
                raise ValueError('调整 LUT 必须使用 0 至 255 的整数。')
        return np.array(tables, dtype=np.uint8)
    if kind == 'threshold':
        return _number(spec.get('threshold'), '阈值', 1, 255, True)
    if kind == 'huesaturation':
        return _validate_hsl(spec)
    try:
        table = np.asarray(spec.get('lut'), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError('渐变映射 LUT 数据无效。') from exc
    if table.shape != (4097, 3) or not np.isfinite(table).all() or np.any(table < 0) or np.any(table > 255):
        raise ValueError('渐变映射 LUT 必须包含 4097 个 RGB 值。')
    return table, _flag(spec.get('dithered'), '渐变仿色')


def apply_adjustment_rgba(canvas, spec, mask=None, opacity=255):
    """Return a new RGBA image, retaining alpha and never mutating its inputs.

    The optional same-size mask is converted to L. Opacity multiplies coverage
    using the same 8-bit rounding as the existing brightness/contrast path.
    Large images are processed in row blocks to bound NumPy scratch memory.
    """
    settings = _validated_spec(spec)
    opacity = _number(opacity, '调整图层不透明度', 0, 255, True)
    if mask is not None and mask.size != canvas.size:
        raise ValueError('Adjustment mask must have the same size as the canvas.')
    source = canvas.convert('RGBA')
    if opacity == 0:
        return source.copy()
    rgb_image = source.convert('RGB')
    kind = spec['type']
    if kind in _CHANNEL_KINDS:
        adjusted = rgb_image.point(settings.reshape(-1).tolist())
    else:
        adjusted = Image.new('RGB', source.size)
        for top in range(0, source.height, 256):
            box = (0, top, source.width, min(source.height, top+256))
            rgb = np.asarray(rgb_image.crop(box))
            if kind == 'huesaturation':
                values = _apply_hsl(rgb, settings)
            elif kind == 'threshold':
                luminance = np.rint(rgb @ _LUMA)
                threshold = (settings-1/255)*255/256
                values = np.repeat((luminance > threshold)[..., None], 3, axis=2)*255
            else:
                table, dither = settings
                tone = rgb @ _LUMA
                if dither:
                    # Photoshop reference ramps show slope-dependent noise:
                    # perturb the sampled tone, not the resulting RGB color.
                    y, x = np.ogrid[top:box[3], :source.width]
                    noise = (((x*73856093) ^ (y*19349663)) & 65535) / 32768 - 1
                    tone = tone+noise
                index = np.clip(tone * 4096/255, 0, 4096)
                lower = np.minimum(index.astype(np.int32), 4095)
                fraction = (index-lower)[..., None]
                values = table[lower]*(1-fraction)+table[lower+1]*fraction
            adjusted.paste(Image.fromarray(np.rint(values).clip(0, 255).astype(np.uint8)), (0, top))
    if mask is None and opacity == 255:
        result = adjusted
    else:
        coverage = Image.new('L', source.size, opacity) if mask is None else mask.convert('L')
        if mask is not None and opacity != 255:
            coverage = coverage.point([round(v*opacity/255) for v in range(256)])
        result = Image.composite(adjusted, rgb_image, coverage)
    result.putalpha(source.getchannel('A'))
    return result
