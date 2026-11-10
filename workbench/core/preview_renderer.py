"""Small interactive renderers derived from immutable, decoded PSD resources."""
import copy

import numpy as np
from PIL import Image
from engine import apply_opacity, coefficients, shadow_image


def _scaled_size(size, factor):
    return tuple(max(1, round(value * factor)) for value in size)


def _scaled_warp(mapping, factor):
    """UVs are normalized uint16 coordinates; only their destination grid shrinks."""
    size = _scaled_size(mapping['u'].shape[::-1], factor)
    metadata = copy.deepcopy(mapping['metadata'])
    metadata['canvas'] = list(size)
    if 'native' in metadata:
        metadata['native'] = list(_scaled_size(metadata['native'], factor))
    metadata.pop('coverage_bbox', None)
    if factor == 1:
        return dict(metadata=metadata, **{key: mapping[key].copy() for key in ('u', 'v', 'coverage')})
    coverage = mapping['coverage'].astype(np.float32)
    def resize(values):
        with Image.fromarray(values) as image:
            with image.resize(size, Image.Resampling.BOX) as small:
                return np.asarray(small, dtype=np.float32).copy()
    weights = resize(coverage)
    result = {'metadata': metadata, 'coverage': np.rint(weights).astype(np.uint8)}
    for axis in ('u', 'v'):
        weighted = resize(mapping[axis].astype(np.float32) * coverage)
        uv = np.divide(weighted, weights, out=np.zeros_like(weights), where=weights > 0)
        result[axis] = np.rint(np.clip(uv, 0, 65535)).astype(np.uint16)
    return result


def scaled_renderer(original, max_edge):
    if type(max_edge) is not int or max_edge < 1:
        raise ValueError('预览尺寸必须为正整数')
    largest = max(max(scene['width'], scene['height']) for scene in original.data['scenes'])
    factor = min(1, max_edge / largest)
    renderer = copy.copy(original)
    renderer.data = copy.deepcopy(original.data)
    renderer.assets = {name: image.resize(_scaled_size(image.size, factor), Image.Resampling.LANCZOS)
                       for name, image in original.assets.items()}
    renderer.warp_maps = {name: _scaled_warp(mapping, factor) for name, mapping in original.warp_maps.items()}
    renderer._fixed_images = {}
    renderer._fixed_settings = None
    renderer._static_layers = {}

    def walk(ops):
        for op in ops:
            if op.get('shadow'):
                for key in ('dx', 'dy', 'blur', 'spread'):
                    if key in op['shadow']:
                        op['shadow'][key] *= factor
            if op['kind'] == 'slot':
                # Keep discrete fit/orientation decisions in the original print plane.
                op['preview_native_size'] = list(op['native_size'])
                op['native_size'] = list(_scaled_size(op['native_size'], factor))
                op['quad'] = [[x * factor, y * factor] for x, y in op['quad']]
                op['coeff'] = coefficients(op['quad'], op['native_size'])
            if op.get('ops'):
                walk(op['ops'])
            if op.get('base_op'):
                walk([op['base_op']])
            if op['kind'] == 'static':
                layer = renderer.assets[op['asset']]
                if op.get('mask'):
                    from PIL import ImageChops
                    layer = layer.copy()
                    layer.putalpha(ImageChops.multiply(layer.getchannel('A'), renderer.assets[op['mask']]))
                shadow = op.get('shadow')
                if shadow:
                    shadow = dict(shadow, opacity=shadow['opacity'] * op.get('opacity', 255) / 255)
                renderer._static_layers[id(op)] = (apply_opacity(layer, op.get('opacity', 255)), shadow_image(layer, shadow))

    for scene in renderer.data['scenes']:
        scene['width'], scene['height'] = _scaled_size((scene['width'], scene['height']), factor)
        walk(scene['ops'])
    return renderer
