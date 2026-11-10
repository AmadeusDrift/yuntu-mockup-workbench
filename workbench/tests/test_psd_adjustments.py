"""RGB8 adjustment behavior, serialization, and fail-closed PSD parsing."""
import importlib
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'workbench'))
import bootstrap
import numpy as np
from PIL import Image
from psd_tools.constants import ColorMode
from psd_tools.psd.adjustments import (
    ColorStop, Curves, CurvesExtraItem, CurvesExtraMarker, GradientMap,
    HueSaturation, LevelRecord, Levels, TransparencyStop,
)


def layer(kind, **kwargs):
    return SimpleNamespace(kind=kind, _psd=SimpleNamespace(depth=8, color_mode=ColorMode.RGB), **kwargs)


def gradient(colors=((0, (0, 0, 0)), (4096, (65535, 65535, 65535))), **kwargs):
    params = dict(version=1, interpolation=4096,
                  color_stops=[ColorStop(pos, 50, 0, tuple(color) + (0,)) for pos, color in colors],
                  transparency_stops=[TransparencyStop(0, 50, 255), TransparencyStop(4096, 50, 255)])
    params.update(kwargs)
    return layer('gradientmap', _data=GradientMap(**params))


def level(**kwargs):
    params = dict(input_floor=0, input_ceiling=255, output_floor=0, output_ceiling=255, gamma=100)
    params.update(kwargs)
    return LevelRecord(**params)


def levels(*records):
    return layer('levels', data=Levels(version=2, items=list(records)))


def curves(records, is_map=False, version=1, extra=True):
    data = Curves(version=version, is_map=is_map,
                  count_map=sum(1 << ch for ch in records) if version == 1 else len(records),
                  data=list(records.values()),
                  extra=CurvesExtraMarker(items=[CurvesExtraItem(ch, pts) for ch, pts in records.items()]) if extra else None)
    return layer('curves', data=data, extra=data.extra)


def hsl(master=(0, 0, 0), ranges=(), colorize=False, colorization=(0, 0, 0)):
    data = HueSaturation(version=2, enable=int(colorize), colorization=colorization,
                         master=master, items=list(ranges))
    return layer('huesaturation', _data=data, master=master, data=list(ranges),
                 enable_colorization=int(colorize), colorization=colorization)


def pixels(values):
    out = Image.new('RGBA', (len(values), 1))
    out.putdata(values)
    return out


METAL_STOPS = ((0, (1284, 1284, 1542)), (562, (3340, 3340, 3598)),
               (1446, (7966, 7966, 8224)), (2490, (13878, 13878, 14134)),
               (3245, (19532, 19532, 20046)), (3694, (23130, 23130, 23644)),
               (3967, (28012, 28012, 28526)), (4096, (34179, 34179, 34693)))


class AdjustmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.module = importlib.import_module('psd_adjustments')
        except ModuleNotFoundError:
            cls.module = None

    def setUp(self):
        self.assertIsNotNone(self.module, 'The isolated PSD adjustment module must be implemented.')

    def apply(self, value, image):
        spec = self.module.compile_adjustment(value)
        restored = json.loads(json.dumps(spec, allow_nan=False))
        self.assertEqual(restored, spec)
        self.assertEqual(spec['type'], value.kind)
        return self.module.apply_adjustment_rgba(image, restored)

    def test_supported_kinds_are_explicit_and_do_not_replace_brightness_contrast(self):
        self.assertEqual(self.module.SUPPORTED_KINDS, {
            'gradientmap', 'levels', 'curves', 'huesaturation',
            'exposure', 'invert', 'posterize', 'threshold'})

    def test_invert_preserves_alpha_and_does_not_modify_input_or_spec(self):
        image = pixels([(0, 127, 255, 0), (200, 50, 10, 128), (14, 19, 30, 255)])
        before = image.tobytes()
        output = self.apply(layer('invert'), image)
        self.assertEqual(list(output.get_flattened_data()), [(255, 128, 0, 0), (55, 205, 245, 128), (241, 236, 225, 255)])
        self.assertEqual(image.tobytes(), before)

    def test_mask_and_opacity_multiply_coverage_preserving_alpha(self):
        image = pixels([(40, 100, 200, 0), (40, 100, 200, 128), (40, 100, 200, 255)])
        mask = Image.new('L', (3, 1)); mask.putdata([0, 128, 255])
        before = mask.tobytes()
        spec = self.module.compile_adjustment(layer('invert'))
        output = self.module.apply_adjustment_rgba(image, spec, mask, 128)
        expected = []
        for alpha, coverage in zip((0, 128, 255), (0, 64, 128)):
            expected.append(tuple(round(a + (b-a) * coverage/255) for a, b in zip((40, 100, 200), (215, 155, 55))) + (alpha,))
        self.assertEqual(list(output.get_flattened_data()), expected)
        self.assertEqual(mask.tobytes(), before)
        zero = self.module.apply_adjustment_rgba(image, spec, opacity=0)
        self.assertEqual(zero.tobytes(), image.tobytes())
        self.assertIsNot(zero, image)

    def test_default_adjustments_are_identity_for_entire_rgb8_domain(self):
        image = pixels([(v, 255-v, (v * 31) % 256, v) for v in range(256)])
        values = [levels(level()), curves({0: [(0, 0), (255, 255)]}), hsl(),
                  layer('exposure', exposure=0, exposure_offset=0, gamma=1)]
        for value in values:
            with self.subTest(kind=value.kind):
                self.assertEqual(self.apply(value, image).tobytes(), image.tobytes())

    def test_levels_input_gamma_output_and_per_channel_order(self):
        image = pixels([(64, 128, 192, 77)])
        output = self.apply(levels(level(output_floor=20, output_ceiling=220),
                                   level(input_floor=64, input_ceiling=192),
                                   level(gamma=200), level()), image)
        # Channels are adjusted first, then the master; intermediate values use 8-bit bins.
        self.assertEqual(output.getpixel((0, 0)), (20, 161, 171, 77))

    def test_levels_inverted_output_and_extreme_valid_gamma_are_finite(self):
        image = pixels([(0, 128, 255, 128)])
        self.assertEqual(self.apply(levels(level(output_floor=255, output_ceiling=0)), image).getpixel((0, 0)), (255, 127, 0, 128))
        for gamma in (1, 999):
            output = self.apply(levels(level(gamma=gamma)), image)
            self.assertEqual(output.getpixel((0, 0))[0::2], (0, 255))

    def test_curves_uses_output_input_point_order_and_clamps_endpoints(self):
        image = pixels([(0, 64, 255, 91), (128, 128, 128, 255)])
        output = self.apply(curves({0: [(20, 32), (220, 224)]}), image)
        self.assertEqual(list(output.get_flattened_data()), [(20, 53, 220, 91), (120, 120, 120, 255)])

    def test_curves_passes_nonlinear_control_point_and_applies_channel_before_master(self):
        value = curves({0: [(0, 0), (200, 128), (255, 255)], 1: [(255, 0), (0, 255)]})
        self.assertEqual(self.apply(value, pixels([(127, 128, 0, 50)])).getpixel((0, 0)), (200, 200, 0, 50))

    def test_curve_map_and_legacy_bitmap_records_work_without_extra(self):
        image = pixels([(0, 128, 255, 90)])
        maps = curves({1: list(reversed(range(256)))}, is_map=True, extra=False)
        self.assertEqual(self.apply(maps, image).getpixel((0, 0)), (255, 128, 255, 90))
        identity = curves({0: [(0, 0), (255, 255)]}, version=4, extra=False)
        self.assertEqual(self.apply(identity, image).tobytes(), image.tobytes())

    def test_exposure_operates_in_linear_light_with_srgb_gamma_approximation(self):
        image = pixels([(32, 64, 128, 113)])
        output = self.apply(layer('exposure', exposure=1, exposure_offset=0, gamma=1), image)
        expected = tuple(round(min(1, (v/255)**2.2 * 2)**(1/2.2) * 255) for v in (32, 64, 128)) + (113,)
        self.assertEqual(output.getpixel((0, 0)), expected)
        for exposure, offset, gamma in ((20, 0.5, .01), (-20, -0.5, 9.99)):
            self.apply(layer('exposure', exposure=exposure, exposure_offset=offset, gamma=gamma), image)

    def test_posterize_quantizes_each_channel_into_requested_bins(self):
        image = pixels([(0, 63, 64, 32), (127, 128, 191, 64), (192, 254, 255, 128)])
        output = self.apply(layer('posterize', posterize=4), image)
        self.assertEqual(list(output.get_flattened_data()), [(0, 0, 85, 32), (85, 170, 170, 64), (255, 255, 255, 128)])

    def test_threshold_uses_luminance_and_threshold_boundary(self):
        image = pixels([(127, 127, 127, 32), (128, 128, 128, 64), (255, 0, 0, 128), (0, 255, 0, 255)])
        output = self.apply(layer('threshold', threshold=128), image)
        self.assertEqual(list(output.get_flattened_data()), [(0, 0, 0, 32), (255, 255, 255, 64), (0, 0, 0, 128), (255, 255, 255, 255)])

    def test_hue_rotation_desaturation_and_lightness_extremes(self):
        image = pixels([(255, 0, 0, 80), (0, 255, 0, 160), (128, 128, 128, 240)])
        self.assertEqual(list(self.apply(hsl((120, 0, 0)), image).get_flattened_data()), [(0, 255, 0, 80), (0, 0, 255, 160), (128, 128, 128, 240)])
        self.assertEqual(self.apply(hsl((0, -100, 0)), image).getpixel((0, 0)), (128, 128, 128, 80))
        for lightness, color in ((-100, 0), (100, 255)):
            self.assertEqual(self.apply(hsl((0, 0, lightness)), image).getpixel((1, 0)), (color, color, color, 160))

    def test_selective_hue_wraps_red_range_and_does_not_change_green(self):
        image = pixels([(255, 0, 0, 77), (0, 255, 0, 99)])
        value = hsl(ranges=[((315, 345, 15, 45), (120, 0, 0))])
        self.assertEqual(list(self.apply(value, image).get_flattened_data()), [(0, 255, 0, 77), (0, 255, 0, 99)])

    def test_selective_desaturation_and_overlapping_ranges_are_bounded(self):
        image = pixels([(255, 0, 0, 150), (0, 0, 255, 200)])
        red = ((315, 345, 15, 45), (0, -100, 0))
        output = self.apply(hsl(ranges=[red]), image)
        self.assertEqual(output.getpixel((0, 0)), (128, 128, 128, 150))
        self.assertEqual(output.getpixel((1, 0)), (0, 0, 255, 200))
        self.apply(hsl(ranges=[red, red]), image)

    def test_colorize_changes_gray_using_absolute_hue_and_saturation(self):
        output = self.apply(hsl(colorize=True, colorization=(120, 100, 0)), pixels([(128, 128, 128, 33)]))
        self.assertEqual(output.getpixel((0, 0)), (1, 255, 1, 33))

    def test_adjustments_match_independent_photoshop_26_golden_pixels(self):
        # Native Photoshop 26.0.0 exports from an owned RGB8 color-ramp fixture.
        # Samples: (0,0),(109,0),(110,0),(255,0),(64,32),(109,96),(192,96),(255,127).
        # Constants deliberately require neither Photoshop nor fixture files.
        source = [(0, 0, 0), (109, 109, 109), (110, 110, 110), (255, 255, 255),
                  (64, 32, 191), (146, 200, 109), (63, 200, 192), (0, 200, 255)]
        expected = {
            'invert': [(255, 255, 255), (146, 146, 146), (145, 145, 145), (0, 0, 0),
                       (191, 223, 64), (109, 55, 146), (192, 55, 63), (255, 55, 0)],
            'posterize': [(0, 0, 0), (85, 85, 85), (85, 85, 85), (255, 255, 255),
                         (85, 0, 170), (170, 255, 85), (0, 255, 255), (0, 255, 255)],
            'threshold': [(0, 0, 0), (0, 0, 0), (255, 255, 255), (255, 255, 255),
                          (0, 0, 0), (255, 255, 255), (255, 255, 255), (255, 255, 255)],
            'exposure': [(51, 51, 51), (141, 141, 141), (142, 142, 142), (255, 255, 255),
                         (93, 64, 229), (181, 238, 141), (92, 238, 230), (51, 238, 255)],
            'huesaturation': [(0, 0, 0), (98, 98, 98), (99, 99, 99), (230, 230, 230),
                              (124, 0, 201), (80, 197, 80), (31, 143, 206), (0, 84, 230)],
            'levels': [(10, 10, 10), (123, 123, 123), (124, 124, 124), (240, 240, 240),
                       (72, 32, 204), (161, 213, 123), (71, 213, 205), (10, 213, 240)],
            'curves': [(0, 0, 0), (107, 107, 107), (109, 109, 109), (255, 255, 255),
                       (45, 18, 221), (165, 228, 107), (44, 228, 221), (0, 228, 255)],
        }
        cases = [(layer('invert'), 0), (layer('posterize', posterize=4), 0),
                 (layer('threshold', threshold=110), 0),
                 (layer('exposure', exposure=.5, exposure_offset=.02, gamma=1.1), 0),
                 (hsl((25, 30, -10)), 3),
                 (levels(level(input_floor=20, input_ceiling=230, gamma=120,
                               output_floor=10, output_ceiling=240)), 1),
                 (curves({0: [(0, 0), (45, 64), (210, 180), (255, 255)]}), 0)]
        image = pixels([rgb + (255,) for rgb in source])
        for value, tolerance in cases:
            with self.subTest(kind=value.kind):
                output = self.apply(value, image)
                actual = np.asarray(output, dtype=np.int16)[0, :, :3]
                difference = np.abs(actual-np.array(expected[value.kind], dtype=np.int16))
                self.assertLessEqual(int(difference.max()), tolerance)
                self.assertEqual(output.getchannel('A').tobytes(), image.getchannel('A').tobytes())

    def test_classic_gradient_smoothness_midpoint_reverse_and_source_alpha(self):
        image = pixels([(0, 0, 0, 0), (64, 64, 64, 64), (128, 128, 128, 128), (255, 255, 255, 255)])
        linear = self.apply(gradient(interpolation=0), image)
        self.assertEqual(linear.tobytes(), image.tobytes())
        smooth = self.apply(gradient(), image)
        self.assertEqual(smooth.getpixel((1, 0)), (52, 52, 52, 64))
        reverse = self.apply(gradient(interpolation=0, is_reversed=1), image)
        self.assertEqual(reverse.getpixel((1, 0)), (191, 191, 191, 64))
        shifted = gradient(interpolation=0)
        shifted._data.color_stops[0].midpoint = 25
        self.assertEqual(self.apply(shifted, image).getpixel((1, 0)), (128, 128, 128, 64))

    def test_gradient_channels_and_nonendpoint_stops_are_clamped(self):
        value = gradient(colors=((1024, (65535, 0, 0)), (3072, (0, 0, 65535))))
        output = self.apply(value, pixels([(0, 0, 0, 31), (255, 255, 255, 47)]))
        self.assertEqual(list(output.get_flattened_data()), [(255, 0, 0, 31), (0, 0, 255, 47)])

    def test_supplied_black_metal_gradient_config_is_serializable_and_effective(self):
        value = gradient(colors=METAL_STOPS, is_dithered=1, color_model=3, roughness=2048,
                         random_seed=1262508, minimum_color=[0]*4, maximum_color=[32768]*4)
        image = pixels([(0, 0, 0, 10), (128, 128, 128, 20), (255, 255, 255, 30)])
        output = self.apply(value, image)
        self.assertLessEqual(max(abs(a-b) for a, b in zip(output.getpixel((0, 0))[:3], (5, 5, 6))), 1)
        self.assertTrue(35 <= output.getpixel((1, 0))[0] <= 55)
        self.assertLessEqual(max(abs(a-b) for a, b in zip(output.getpixel((2, 0))[:3], (133, 133, 135))), 1)
        self.assertEqual(output.getchannel('A').tobytes(), image.getchannel('A').tobytes())

    def test_gradient_dither_is_deterministic_and_bounded_to_one_level(self):
        image = Image.new('RGBA', (32, 32), (127, 127, 127, 123))
        plain = self.apply(gradient(interpolation=0), image)
        value = gradient(interpolation=0, is_dithered=1)
        first, second = self.apply(value, image), self.apply(value, image)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertGreater(len(set(first.getchannel('R').get_flattened_data())), 1)
        self.assertLessEqual(np.abs(np.asarray(first).astype(int)-np.asarray(plain).astype(int)).max(), 1)

    def test_native_gradient_dither_varies_with_slope_and_leaves_black_endpoint_stable(self):
        # Photoshop 26 export of the supplied gradient on a 256x64 gray ramp:
        # gray0 -> red5 on every row; gray250 -> red115..122. This establishes
        # dither before gradient sampling, rather than adding noise to output RGB.
        value = gradient(colors=METAL_STOPS, is_dithered=1)
        image = Image.new('RGBA', (64, 64), (250, 250, 250, 77))
        result = self.apply(value, image)
        low, high = result.getchannel('R').getextrema()
        self.assertGreaterEqual(high-low, 5)
        self.assertTrue(114 <= low <= 116 and 121 <= high <= 123)
        black = self.apply(value, Image.new('RGBA', (64, 64), (0, 0, 0, 77)))
        self.assertEqual(black.getchannel('R').getextrema(), (5, 5))

    def test_unknown_types_color_modes_and_malformed_records_raise(self):
        invalid = [layer('colorlookup'), layer('levels', data=None), layer('curves', data=None),
                   layer('exposure', exposure=float('nan'), exposure_offset=0, gamma=1),
                   layer('posterize', posterize=1), layer('threshold', threshold=0),
                   levels(level(input_floor=128, input_ceiling=128)),
                   curves({0: [(0, 20), (255, 20)]}), curves({7: [(0, 0), (255, 255)]}),
                   hsl((181, 0, 0)), hsl(ranges=[((0, 90, 45, 120), (5, 0, 0))])]
        for depth, mode in ((16, ColorMode.RGB), (8, ColorMode.CMYK), (8, ColorMode.GRAYSCALE)):
            value = layer('invert'); value._psd = SimpleNamespace(depth=depth, color_mode=mode); invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.compile_adjustment(value)

    def test_unsupported_gradient_variants_fail_instead_of_becoming_identity(self):
        invalid = []
        for attribute, value in (('version', 9), ('method', b'Lnr '), ('mode', 1),
                                 ('interpolation', 4097), ('is_reversed', 2)):
            item = gradient(); object.__setattr__(item._data, attribute, value); invalid.append(item)
        item = gradient(); item._data.color_stops[0].mode = 2; invalid.append(item)
        item = gradient(); item._data.transparency_stops[0].opacity = 128; invalid.append(item)
        item = gradient(); item._data.color_stops[1].location = 0; invalid.append(item)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.compile_adjustment(value)

    def test_application_rejects_unknown_or_corrupt_specs_and_invalid_coverage(self):
        image = Image.new('RGBA', (2, 2))
        for spec in ({'type': 'unknown'}, {'type': 'invert'}, {'type': 'levels', 'luts': [[0]*2]}):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                self.module.apply_adjustment_rgba(image, spec)
        spec = self.module.compile_adjustment(layer('invert'))
        for opacity in (-1, 256, 1.5):
            with self.subTest(opacity=opacity), self.assertRaises(ValueError):
                self.module.apply_adjustment_rgba(image, spec, opacity=opacity)
        with self.assertRaises(ValueError):
            self.module.apply_adjustment_rgba(image, spec, mask=Image.new('L', (1, 1)))


if __name__ == '__main__':
    unittest.main()

