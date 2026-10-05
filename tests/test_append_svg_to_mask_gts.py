import tempfile
import unittest
from pathlib import Path

from tools.append_svg_to_mask_gts import (
    GerberFormat,
    build_one_svg_snippet,
    classify_contour_polarities,
    compute_viewbox_mapping,
    load_svg_shapes,
    parse_svg_length_to_mm,
    read_svg_physical_size_mm,
)


class SvgCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.fmt = GerberFormat("MM", 4, 6, "L", "A")

    def write_svg(self, body: str, root_attrs: str = 'width="19mm" height="11mm" viewBox="0 0 19 11"') -> Path:
        path = self.root / "input.svg"
        path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" {root_attrs}>{body}</svg>',
            encoding="utf-8",
        )
        return path

    def test_unitless_root_lengths_are_css_pixels(self):
        self.assertAlmostEqual(parse_svg_length_to_mm("96"), 25.4)
        self.assertAlmostEqual(parse_svg_length_to_mm("1e2px"), 100 * 25.4 / 96)

    def test_percentage_canvas_uses_configured_physical_fallback(self):
        svg = self.write_svg('<path d="M0 0H19V11H0Z"/>', 'width="100%" height="100%" viewBox="0 0 19 11"')
        self.assertEqual(read_svg_physical_size_mm(svg, (19, 11)), (19, 11))

    def test_nested_transforms_are_flattened_and_hidden_shapes_are_skipped(self):
        svg = self.write_svg(
            '<g transform="translate(5,2)"><g transform="scale(2)">'
            '<rect x="0" y="0" width="2" height="1"/></g></g>'
            '<g display="none"><path d="M0 0H19V11H0Z"/></g>'
            '<rect x="0" y="0" width="19" height="11" fill="none"/>'
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 1)
        self.assertEqual(tuple(round(value, 6) for value in shapes[0].path.bbox()), (5.0, 9.0, 2.0, 4.0))

    def test_basic_shapes_are_supported(self):
        svg = self.write_svg(
            '<rect x="1" y="1" width="2" height="2"/>'
            '<circle cx="6" cy="2" r="1"/>'
            '<ellipse cx="10" cy="2" rx="2" ry="1"/>'
            '<polygon points="13,1 15,1 14,3"/>'
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 4)

    def test_visible_strokes_fail_with_actionable_message(self):
        svg = self.write_svg('<path d="M1 1H18" fill="none" stroke="black"/>')
        with self.assertRaisesRegex(ValueError, "convert strokes to outlined filled paths"):
            load_svg_shapes(svg)

    def test_live_text_fails_clearly(self):
        svg = self.write_svg('<text x="1" y="2">Hello</text>')
        with self.assertRaisesRegex(ValueError, "convert text to paths"):
            load_svg_shapes(svg)

    def test_common_embedded_css_is_applied(self):
        svg = self.write_svg(
            '<style>.art { fill: black; stroke: none; }</style>'
            '<path class="art" fill="none" d="M1 1H2V2H1Z"/>'
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 1)

    def test_hidden_editor_content_does_not_block_visible_artwork(self):
        svg = self.write_svg(
            '<g display="none"><text x="1" y="2">Instructions</text>'
            '<path d="M30 30H40V40Z"/></g>'
            '<path d="M1 1H2V2H1Z"/>'
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 1)

    def test_unfilled_line_is_ignored_but_filled_region_remains(self):
        svg = self.write_svg('<line x1="0" y1="0" x2="19" y2="11"/><path d="M1 1H2V2H1Z"/>')
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 1)

    def test_clipping_fails_instead_of_silently_ignoring_it(self):
        svg = self.write_svg('<path d="M0 0H19V11H0Z" clip-path="url(#crop)"/>')
        with self.assertRaisesRegex(ValueError, "clip-path effects"):
            load_svg_shapes(svg)

    def test_artwork_outside_viewbox_is_clipped_like_normal_svg_rendering(self):
        svg = self.write_svg('<path d="M18 1H20V2H18Z"/>')
        snippet = build_one_svg_snippet(self.fmt, svg, (0, 0), 0, None)
        self.assertIn("X19000000", snippet)
        self.assertNotIn("X20000000", snippet)

    def test_artwork_entirely_outside_canvas_fails_with_useful_message(self):
        svg = self.write_svg('<path d="M20 1H21V2H20Z"/>')
        with self.assertRaisesRegex(ValueError, "no filled artwork remains inside the SVG canvas"):
            build_one_svg_snippet(self.fmt, svg, (0, 0), 0, None)

    def test_named_content_layer_does_not_exclude_other_visible_artwork(self):
        svg = self.write_svg(
            '<g inkscape:groupmode="layer" inkscape:label="GUIDES">'
            '<path id="guide" d="M-1 -1H20V12H-1Z"/></g>'
            '<g inkscape:groupmode="layer" inkscape:label="CONTENT">'
            '<path id="artwork" d="M1 1H2V2H1Z"/></g>',
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            'width="19mm" height="11mm" viewBox="0 0 19 11"',
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 2)
        self.assertEqual({shape.label for shape in shapes}, {"<path id='guide'>", "<path id='artwork'>"})

    def test_empty_content_layer_does_not_block_visible_artwork_elsewhere(self):
        svg = self.write_svg(
            '<g inkscape:groupmode="layer" inkscape:label="PCB"><path d="M0 0H19V11H0Z"/></g>'
            '<g inkscape:groupmode="layer" inkscape:label="CONTENT"/>',
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            'width="19mm" height="11mm" viewBox="0 0 19 11"',
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 1)
        self.assertIn("G36*", build_one_svg_snippet(self.fmt, svg, (0, 0), 0, None))

    def test_generic_named_artwork_group_is_supported(self):
        svg = self.write_svg(
            '<g id="guides"><path d="M0 0H19V11H0Z"/></g>'
            '<g data-name="Artwork"><circle cx="5" cy="5" r="1"/></g>'
        )
        _root, shapes = load_svg_shapes(svg)
        self.assertEqual(len(shapes), 2)

    def test_wrong_physical_size_is_rejected_for_fixed_card_jobs(self):
        svg = self.write_svg(
            '<path d="M1 1H2V2H1Z"/>',
            'width="38mm" height="22mm" viewBox="0 0 19 11"',
        )
        with self.assertRaisesRegex(ValueError, "this job requires 19x11mm"):
            build_one_svg_snippet(self.fmt, svg, (0, 0), 0, None, (19, 11))

    def test_evenodd_and_nonzero_compound_path_polarities(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        inner_same_winding = [(2, 2), (8, 2), (8, 8), (2, 8)]
        polygons = [outer, inner_same_winding]
        areas = [100.0, 36.0]
        self.assertEqual(
            classify_contour_polarities(polygons, areas, "evenodd"),
            [(0, 0, "dark"), (1, 1, "clear")],
        )
        self.assertEqual(
            classify_contour_polarities(polygons, areas, "nonzero"),
            [(0, 0, "dark")],
        )

    def test_default_preserve_aspect_ratio_uses_centered_uniform_scale(self):
        mapping = compute_viewbox_mapping((0, 0, 100, 100), (20, 10), "")
        self.assertEqual((mapping.sx, mapping.sy), (0.1, 0.1))
        self.assertEqual((mapping.offset_x_mm, mapping.offset_y_mm), (5.0, 0.0))


if __name__ == "__main__":
    unittest.main()
