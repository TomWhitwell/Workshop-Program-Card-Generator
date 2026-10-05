# =============================================================================
# append_svg_to_mask_gts.py
#
# PURPOSE
# -------
# Append one or more pre-made SVG “artwork” files into an existing KiCad Gerber
# solder mask layer (typically Front Mask: *.gts), writing a new *.gts output.
#
# This is designed for a low-user workflow where non-electrical artwork is
# authored in Affinity/Illustrator/Inkscape or another vector editor, exported
# as SVG to fixed specs,
# then injected into an otherwise fixed Gerber “shell” set.
#
#
# TECHNICAL APPROACH (WHY THIS EXISTS)
# ------------------------------------
# 1) We read the base GTS file and parse its Gerber format:
#    - Units (MM/IN) from %MO..*%
#    - Coordinate precision from %FS..*%
#
# 2) We convert SVG geometry to Gerber REGION fills:
#    - SVG paths and basic shapes are read via svgpathtools.
#    - Nested transforms and inherited inline presentation styles are resolved.
#    - Invisible shapes are ignored and unsupported paint effects fail early.
#    - Each shape may contain multiple “subpaths” (compound shapes).
#    - We flatten curves into polygons by sampling along each segment.
#    - We emit each polygon as a Gerber region (G36*/G37*).
#
# 3) Correct handling of “holes” in letters (counters):
#    - SVG exporters often represent glyphs (e.g. “0”, “A”, “R”, “O”) as one
#      <path> containing multiple subpaths: an outer contour and one or more
#      inner contours (holes).
#    - In Gerber, holes are commonly represented by switching layer polarity:
#        %LPD*%  = DARK (add material)
#        %LPC*%  = CLEAR (subtract material)
#    - Contours are classified by nesting depth and the effective SVG fill-rule.
#    - “evenodd” alternates dark/clear by nesting depth.
#    - “nonzero” uses accumulated winding to determine dark/clear transitions.
#
# 4) Coordinate mapping:
#    - SVG uses a Y-down coordinate system; KiCad/Gerber viewers commonly use
#      Y-up world coordinates.
#    - We map SVG -> “local mm” using:
#        - viewBox origin as the reference (origin mode: viewBox)
#        - scale/alignment from viewBox, physical size and preserveAspectRatio
#        - a Y-flip to make local Y positive upwards
#    - Placement coordinates are provided in mm in the same coordinate space you
#      see in KiCad/GerbView (e.g. “67.5,31.0”).
#    - We invert board Y by default (KiCad->this script mapping), because that
#      is the behaviour that matched the working setup.
#
# 5) Rotation:
#    - Each placed SVG instance may be rotated by N degrees CCW.
#    - Rotation is around the CENTRE of the SVG’s physical size
#      (width/height in mm), i.e. (W/2, -H/2) in our local coordinate frame.
#
# 6) Output behaviour:
#    - We append generated Gerber commands immediately before the base file’s
#      trailing “M02*” end marker.
#    - The base file content is preserved unchanged otherwise.
#
#
# CLI USAGE (THE ONLY FLAGS YOU SHOULD NEED)
# -----------------------------------------
# Required:
#   --base-gts PATH     Existing mask Gerber to append into (input)
#   --out-gts  PATH     Output filename
#
# Repeat these three as many times as needed (lists must be same length):
#   --svg     PATH      SVG file to place (one instance per repetition)
#   --place   X,Y       Placement in mm (KiCad/GerbView coords)
#   --rotate  DEG       Rotation in degrees CCW around SVG centre
#
# Optional:
#   --default-svg-size-mm W,H
#       Fallback physical size in mm if an SVG lacks width/height attributes.
#       If your SVGs already have width/height (e.g. “19mm”/“11mm”), you can
#       omit this.
#   --expected-svg-size-mm W,H
#       Reject resolved SVG sizes other than W,H. The card workflow uses this
#       to prevent valid but incorrectly sized artwork crossing card boundaries.
#
# Example:
#   python ./append_svg_to_mask_gts.py \
#     --base-gts "../.../4-up-blank-F_Mask.gts" \
#     --out-gts  "4-up-blank-F_Mask.NEW.gts" \
#     --svg "../.../turing-resave.svg" --place 67.5,31.0 --rotate 0 \
#     --svg "../.../turing-resave.svg" --place 67.5,44.0 --rotate 0 \
#     --svg "../.../reverb-resave.svg" --place 78.5,31.0 --rotate 180 \
#     --svg "../.../8mu-resave.svg"    --place 78.5,44.0 --rotate 180
#
# Notes:
# - The i’th --svg is placed at the i’th --place and rotated by the i’th --rotate.
# - If counts do not match, the script exits with an error.
#
#
# SVG REQUIREMENTS (AUTHORING RULES)
# ---------------------------------
# These rules keep the conversion reliable and predictable:
#
# 1) The SVG MUST have a viewBox attribute.
#    Example:
#      <svg ... width="19mm" height="11mm" viewBox="0 0 53.8583 31.1811">
#
# 2) The SVG SHOULD have width/height in absolute units (mm recommended).
#    - width="19mm" height="11mm" is ideal.
#    - If missing, provide --default-svg-size-mm W,H on the command line.
#
# 3) Artwork must be filled vector shapes. Paths, rectangles, circles, ellipses,
#    polygons and filled polylines are supported.
#    - Visible strokes are rejected; convert them to outlined filled shapes.
#
# 4) Fonts must be outlined/converted to paths in the design tool.
#
# 5) CSS stylesheets, <use>, nested <svg>, clipping, masks, filters, raster
#    images and foreign content are rejected because they cannot be translated
#    faithfully by this deterministic Gerber converter. Inline presentation
#    attributes/styles and nested SVG transform attributes are supported.
#
# 6) No electrical relevance:
#    - This is intended for solder mask art. It does not check clearances,
#      min feature sizes, polarity rules, or manufacturability.
#    - You are responsible for the SVG creation rules and any DFM constraints.
#
#
# TROUBLESHOOTING
# ---------------
# - “Artwork is displaced or huge”
#     Parent transforms must be applied before viewBox scaling. The parser does
#     this and rejects any resulting geometry that escapes the SVG canvas.
#
# - “Holes in 0/A/R are wrong”
#     The effective evenodd/nonzero fill-rule is honored for compound paths. If
#     holes are separate overlapping objects, combine them before export.
#
# - “Scale is wrong”
#     Check SVG width/height and viewBox are consistent. If width/height are
#     missing or in px, consider exporting with mm units or supply
#     --default-svg-size-mm.
#
#
# DEPENDENCIES
# ------------
# Python 3.10+ recommended.
#
# Required pip packages:
#   pip install svgpathtools
#
# (All other imports are from the standard library.)
#
# =============================================================================



#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from svgpathtools import CONVERSIONS, Document  # type: ignore
from svgpathtools.path import Path as SvgPathT  # type: ignore


SVG_NS = "http://www.w3.org/2000/svg"
SUPPORTED_SHAPE_TAGS = ("path", "rect", "circle", "ellipse", "polygon", "polyline", "line")
SUPPORTED_CONVERSIONS = {name: CONVERSIONS[name] for name in SUPPORTED_SHAPE_TAGS}
NON_RENDERING_CONTAINERS = {"defs", "clipPath", "mask", "marker", "pattern", "symbol"}
UNSUPPORTED_RENDERED_TAGS = {
    "text": "convert text to paths/outlines",
    "image": "embed raster images as vector paths",
    "use": "expand cloned <use> content to ordinary paths",
    "foreignObject": "convert foreign content to paths",
}


# ----------------------------
# Gerber parsing / formatting
# ----------------------------

@dataclass(frozen=True)
class GerberFormat:
    units: str                # "MM" or "IN"
    int_digits: int
    dec_digits: int
    zero_omission: str        # "L" or "T"
    abs_inc: str              # "A" or "I"

    @property
    def scale(self) -> float:
        return 10 ** self.dec_digits

    def mm_to_units(self, mm: float) -> float:
        if self.units.upper() == "MM":
            return mm
        return mm / 25.4

    def fmt_coord(self, value_in_units: float) -> str:
        scaled = int(round(value_in_units * self.scale))
        return f"{scaled:d}"


FSL_RE = re.compile(r"%FS([LT])([AI])X(\d)(\d)Y(\d)(\d)\*%")
MO_RE = re.compile(r"%MO(IN|MM)\*%")


def parse_gerber_format(gts_text: str) -> GerberFormat:
    mo = MO_RE.search(gts_text)
    if not mo:
        raise ValueError("Base GTS: missing %MO(IN|MM)*% units statement")
    units = mo.group(1)

    fs = FSL_RE.search(gts_text)
    if not fs:
        raise ValueError("Base GTS: missing %FS..*% format statement")

    return GerberFormat(
        units=units,
        int_digits=int(fs.group(3)),
        dec_digits=int(fs.group(4)),
        zero_omission=fs.group(1),
        abs_inc=fs.group(2),
    )


def find_m02_insert_index(gts_text: str) -> int:
    idx = gts_text.rfind("M02*")
    if idx == -1:
        raise ValueError("Base GTS: could not find M02* end-of-file marker")
    return idx


# ----------------------------
# SVG helpers
# ----------------------------

def parse_svg_length_to_mm(value: str) -> Optional[float]:
    """
    Parse an SVG length like '19mm', '11mm', '200px', '2.54cm', '1in'.
    Returns mm, or None if empty/unparseable.
    """
    if not value:
        return None
    s = value.strip()
    m = re.match(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z%]*)$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()

    # SVG/CSS absolute units; unitless outer dimensions are CSS px.
    if unit == "mm":
        return num
    if unit == "cm":
        return num * 10.0
    if unit == "in":
        return num * 25.4
    if unit == "pt":  # 72 pt per inch
        return num * (25.4 / 72.0)
    if unit == "pc":  # 6 pc per inch
        return num * (25.4 / 6.0)
    if unit in ("px", ""):  # 96 CSS px per inch
        return num * (25.4 / 96.0)

    return None


def read_svg_root(svg_path: Path) -> ET.Element:
    txt = svg_path.read_text(encoding="utf-8", errors="replace")
    return ET.fromstring(txt)


def read_svg_viewbox(svg_path: Path) -> Tuple[float, float, float, float]:
    root = read_svg_root(svg_path)
    vb = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if not vb:
        raise ValueError(f"{svg_path.name}: missing viewBox (required for reliable scaling)")
    try:
        parts = [float(x) for x in re.split(r"[,\s]+", vb.strip()) if x]
    except ValueError as exc:
        raise ValueError(f"{svg_path.name}: malformed viewBox='{vb}'") from exc
    if len(parts) != 4 or not all(math.isfinite(x) for x in parts):
        raise ValueError(f"{svg_path.name}: malformed viewBox='{vb}'")
    if parts[2] <= 0 or parts[3] <= 0:
        raise ValueError(f"{svg_path.name}: viewBox width/height must be positive, got '{vb}'")
    return parts[0], parts[1], parts[2], parts[3]


def read_svg_physical_size_mm(svg_path: Path, fallback: Optional[Tuple[float, float]]) -> Tuple[float, float]:
    root = read_svg_root(svg_path)
    w_mm = parse_svg_length_to_mm(root.attrib.get("width", ""))
    h_mm = parse_svg_length_to_mm(root.attrib.get("height", ""))
    if w_mm is not None and h_mm is not None and all(
        math.isfinite(x) and x > 0 for x in (w_mm, h_mm)
    ):
        return float(w_mm), float(h_mm)

    if fallback is not None:
        if not all(math.isfinite(x) and x > 0 for x in fallback):
            raise ValueError(f"Fallback SVG size must be positive finite millimetres, got {fallback}")
        return fallback

    raise ValueError(
        f"{svg_path.name}: missing width/height (or unparseable units). "
        "Either ensure width/height are set (e.g. '19mm') or pass --default-svg-size-mm W,H."
    )


@dataclass(frozen=True)
class ViewBoxMapping:
    sx: float
    sy: float
    offset_x_mm: float
    offset_y_mm: float


def compute_viewbox_mapping(
    viewbox: Tuple[float, float, float, float],
    physical_mm: Tuple[float, float],
    preserve_aspect_ratio: str,
) -> ViewBoxMapping:
    _minx, _miny, vb_w, vb_h = viewbox
    w_mm, h_mm = physical_mm
    raw_sx, raw_sy = w_mm / vb_w, h_mm / vb_h

    tokens = (preserve_aspect_ratio or "xMidYMid meet").strip().split()
    if tokens and tokens[0] == "defer":
        tokens = tokens[1:]
    align = tokens[0] if tokens else "xMidYMid"
    meet_or_slice = tokens[1] if len(tokens) > 1 else "meet"
    if len(tokens) > 2 or meet_or_slice not in ("meet", "slice"):
        raise ValueError(f"Unsupported preserveAspectRatio='{preserve_aspect_ratio}'")
    if align == "none":
        return ViewBoxMapping(raw_sx, raw_sy, 0.0, 0.0)
    if not re.fullmatch(r"x(?:Min|Mid|Max)Y(?:Min|Mid|Max)", align):
        raise ValueError(f"Unsupported preserveAspectRatio='{preserve_aspect_ratio}'")

    scale = min(raw_sx, raw_sy) if meet_or_slice == "meet" else max(raw_sx, raw_sy)
    extra_x = w_mm - vb_w * scale
    extra_y = h_mm - vb_h * scale
    x_align = align[1:4]
    y_align = align[5:8]
    x_factor = {"Min": 0.0, "Mid": 0.5, "Max": 1.0}[x_align]
    y_factor = {"Min": 0.0, "Mid": 0.5, "Max": 1.0}[y_align]
    return ViewBoxMapping(scale, scale, extra_x * x_factor, extra_y * y_factor)


@dataclass(frozen=True)
class SvgShape:
    path: SvgPathT
    fill_rule: str
    label: str


def svg_local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def parse_inline_style(value: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for declaration in (value or "").split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            raise ValueError(f"Malformed inline SVG style declaration '{declaration.strip()}'")
        name, raw_value = declaration.split(":", 1)
        result[name.strip().lower()] = re.sub(r"\s*!important\s*$", "", raw_value.strip(), flags=re.IGNORECASE)
    return result


def _selector_matches(element: ET.Element, selector: str) -> bool:
    """Match the simple selectors emitted by common SVG authoring tools."""
    selector = selector.strip()
    match = re.fullmatch(r"(?:(\*|[A-Za-z_][\w.-]*))?(#[\w.-]+)?((?:\.[\w.-]+)*)", selector)
    if not match or not selector:
        raise ValueError(
            f"unsupported CSS selector '{selector}'; save as Plain SVG or convert styles to inline attributes"
        )
    tag, id_selector, class_selectors = match.groups()
    if tag and tag != "*" and svg_local_name(element) != tag:
        return False
    if id_selector and element.attrib.get("id") != id_selector[1:]:
        return False
    required_classes = {value[1:] for value in re.findall(r"\.[\w.-]+", class_selectors)}
    actual_classes = set(element.attrib.get("class", "").split())
    return required_classes.issubset(actual_classes)


def apply_embedded_stylesheets(svg_elements: List[ET.Element]) -> None:
    """Inline straightforward CSS rules used by Illustrator and similar exporters."""
    rules: List[Tuple[Tuple[int, int, int], int, str, Dict[str, str]]] = []
    order = 0
    for style_element in (element for element in svg_elements if svg_local_name(element) == "style"):
        css = re.sub(r"/\*.*?\*/", "", "".join(style_element.itertext()), flags=re.DOTALL)
        if re.search(r"@[A-Za-z-]+", css):
            raise ValueError("SVG CSS at-rules are unsupported; save as Plain SVG or inline the artwork styles")
        consumed = ""
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            consumed += match.group(0)
            declarations = parse_inline_style(match.group(2))
            for selector in match.group(1).split(","):
                selector = selector.strip()
                _selector_matches(style_element, selector)
                id_count = selector.count("#")
                class_count = selector.count(".")
                tag_count = 0 if selector.startswith((".", "#", "*")) else 1
                rules.append(((id_count, class_count, tag_count), order, selector, declarations))
                order += 1
        if re.sub(r"\s+", "", css).strip() and re.sub(r"\s+", "", consumed) != re.sub(r"\s+", "", css):
            raise ValueError("could not parse embedded SVG CSS; save as Plain SVG or inline the artwork styles")

    if not rules:
        return
    for element in svg_elements:
        matched = [rule for rule in rules if _selector_matches(element, rule[2])]
        if not matched:
            continue
        stylesheet_values: Dict[str, str] = {}
        for _specificity, _order, _selector, declarations in sorted(matched, key=lambda rule: (rule[0], rule[1])):
            stylesheet_values.update(declarations)
        existing_inline = element.attrib.get("style", "")
        generated_inline = ";".join(f"{name}:{value}" for name, value in stylesheet_values.items())
        element.attrib["style"] = ";".join(value for value in (generated_inline, existing_inline) if value)


def element_property(element: ET.Element, name: str) -> Optional[str]:
    # Inline CSS has precedence over presentation attributes on the same element.
    styles = parse_inline_style(element.attrib.get("style", ""))
    if name in styles:
        return styles[name]
    value = element.attrib.get(name)
    return value.strip() if value is not None else None


def parse_svg_opacity(value: Optional[str], default: float = 1.0) -> float:
    if value is None or value.strip().lower() == "inherit":
        return default
    s = value.strip()
    try:
        number = float(s[:-1]) / 100.0 if s.endswith("%") else float(s)
    except ValueError as exc:
        raise ValueError(f"Unsupported SVG opacity value '{value}'") from exc
    if not math.isfinite(number):
        raise ValueError(f"Unsupported SVG opacity value '{value}'")
    return min(1.0, max(0.0, number))


def element_chain(
    element: ET.Element,
    parent_map: Dict[ET.Element, ET.Element],
) -> List[ET.Element]:
    chain = [element]
    while chain[-1] in parent_map:
        chain.append(parent_map[chain[-1]])
    chain.reverse()
    return chain


def shape_paint_state(
    element: ET.Element,
    parent_map: Dict[ET.Element, ET.Element],
) -> Tuple[bool, str, bool]:
    """Return (filled_and_visible, fill_rule, visible_stroke)."""
    fill = "black"
    stroke = "none"
    fill_opacity = 1.0
    stroke_opacity = 1.0
    stroke_width = "1"
    visibility = "visible"
    fill_rule = "nonzero"
    opacity_product = 1.0

    chain = element_chain(element, parent_map)
    if any(svg_local_name(node) in NON_RENDERING_CONTAINERS for node in chain[:-1]):
        return False, fill_rule, False

    for node in chain:
        if (element_property(node, "display") or "").strip().lower() == "none":
            return False, fill_rule, False
        if element_property(node, "transform") is not None and "transform" in parse_inline_style(
            node.attrib.get("style", "")
        ):
            raise ValueError("CSS transform properties are unsupported; use the SVG transform attribute")
        opacity_product *= parse_svg_opacity(element_property(node, "opacity"), 1.0)

        for prop_name, current in (
            ("fill", fill),
            ("stroke", stroke),
            ("stroke-width", stroke_width),
            ("visibility", visibility),
            ("fill-rule", fill_rule),
        ):
            specified = element_property(node, prop_name)
            if specified is not None and specified.strip().lower() not in ("inherit", "unset"):
                if prop_name == "fill":
                    fill = specified
                elif prop_name == "stroke":
                    stroke = specified
                elif prop_name == "stroke-width":
                    stroke_width = specified
                elif prop_name == "visibility":
                    visibility = specified
                else:
                    fill_rule = specified

        specified_fill_opacity = element_property(node, "fill-opacity")
        if specified_fill_opacity is not None and specified_fill_opacity.strip().lower() not in ("inherit", "unset"):
            fill_opacity = parse_svg_opacity(specified_fill_opacity)
        specified_stroke_opacity = element_property(node, "stroke-opacity")
        if specified_stroke_opacity is not None and specified_stroke_opacity.strip().lower() not in ("inherit", "unset"):
            stroke_opacity = parse_svg_opacity(specified_stroke_opacity)

        for effect in ("clip-path", "mask", "filter"):
            effect_value = element_property(node, effect)
            if effect_value and effect_value.strip().lower() != "none":
                raise ValueError(
                    f"SVG {effect} effects cannot be represented reliably; expand/flatten the artwork first"
                )

    if visibility.strip().lower() in ("hidden", "collapse") or opacity_product <= 0:
        return False, fill_rule, False

    fill_value = fill.strip().lower()
    filled = fill_value not in ("none", "transparent") and fill_opacity > 0
    stroke_value = stroke.strip().lower()
    try:
        width_number = float(re.match(
            r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
            stroke_width.strip(),
        ).group(0))
    except (AttributeError, ValueError):
        width_number = 1.0
    visible_stroke = stroke_value not in ("none", "transparent") and stroke_opacity > 0 and width_number != 0

    normalized_rule = fill_rule.strip().lower()
    if normalized_rule not in ("nonzero", "evenodd"):
        raise ValueError(f"Unsupported SVG fill-rule '{fill_rule}'")
    return filled, normalized_rule, visible_stroke


def load_svg_shapes(svg_path: Path) -> Tuple[ET.Element, List[SvgShape]]:
    raw_text = svg_path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"<\?xml-stylesheet\b", raw_text, re.IGNORECASE):
        raise ValueError(f"{svg_path.name}: external XML stylesheets are unsupported; inline the artwork styles")

    document = Document(str(svg_path))
    root = document.root
    parent_map = {child: parent for parent in root.iter() for child in parent}
    svg_elements = [element for element in root.iter() if element.tag.startswith("{" + SVG_NS + "}")]

    apply_embedded_stylesheets(svg_elements)
    if sum(svg_local_name(element) == "svg" for element in svg_elements) > 1:
        raise ValueError(f"{svg_path.name}: nested <svg> viewports are unsupported; flatten the artwork first")

    for element in svg_elements:
        tag = svg_local_name(element)
        if tag in UNSUPPORTED_RENDERED_TAGS:
            filled, _rule, stroke = shape_paint_state(element, parent_map)
            if filled or stroke or tag in ("use", "image", "foreignObject"):
                raise ValueError(f"{svg_path.name}: <{tag}> is unsupported; {UNSUPPORTED_RENDERED_TAGS[tag]}")

    included: Dict[int, Tuple[str, str]] = {}
    for element in svg_elements:
        tag = svg_local_name(element)
        if tag not in SUPPORTED_SHAPE_TAGS:
            continue
        filled, fill_rule, visible_stroke = shape_paint_state(element, parent_map)
        label = f"<{tag} id='{element.attrib.get('id', '')}'>"
        if visible_stroke:
            raise ValueError(
                f"{svg_path.name}: {label} has a visible stroke; convert strokes to outlined filled paths"
            )
        # SVG line elements have no fillable interior; an actual line is a stroke.
        if filled and tag != "line":
            included[id(element)] = (fill_rule, label)

    paths = document.paths(
        path_filter=lambda element: id(element) in included,
        path_conversions=SUPPORTED_CONVERSIONS,
    )
    shapes: List[SvgShape] = []
    for path in paths:
        element = getattr(path, "element", None)
        metadata = included.get(id(element))
        if metadata is not None:
            shapes.append(SvgShape(path=path, fill_rule=metadata[0], label=metadata[1]))
    if not shapes:
        raise ValueError(
            f"{svg_path.name}: no visible filled vector artwork was found. "
            "Convert text and strokes to paths, and make sure the artwork is not hidden."
        )
    return root, shapes


# ----------------------------
# Geometry
# ----------------------------

def sample_subpath_to_points(sp: SvgPathT, step_user_units: float) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    step = max(float(step_user_units), 0.01)

    for seg in sp:
        try:
            seg_len = float(seg.length(error=1e-5))
        except Exception:
            seg_len = 1.0

        n = max(2, min(800, int(math.ceil(seg_len / step))))
        for i in range(n):
            t = i / (n - 1)
            z = seg.point(t)
            x = float(z.real)
            y = float(z.imag)
            if not pts or (abs(pts[-1][0] - x) > 1e-9 or abs(pts[-1][1] - y) > 1e-9):
                pts.append((x, y))

    return pts


def clip_polygon_to_rect(
    points: List[Tuple[float, float]],
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> List[Tuple[float, float]]:
    """Clip a sampled SVG contour to its root viewport, as normal SVG rendering does."""
    polygon = list(points)
    if len(polygon) > 1 and all(abs(a - b) < 1e-12 for a, b in zip(polygon[0], polygon[-1])):
        polygon.pop()

    def clip_edge(inside, intersection) -> None:
        nonlocal polygon
        if not polygon:
            return
        output: List[Tuple[float, float]] = []
        previous = polygon[-1]
        previous_inside = inside(previous)
        for current in polygon:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersection(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersection(previous, current))
            previous = current
            previous_inside = current_inside
        polygon = output

    def at_x(first: Tuple[float, float], second: Tuple[float, float], x: float) -> Tuple[float, float]:
        x1, y1 = first
        x2, y2 = second
        if abs(x2 - x1) < 1e-15:
            return x, y1
        return x, y1 + (y2 - y1) * (x - x1) / (x2 - x1)

    def at_y(first: Tuple[float, float], second: Tuple[float, float], y: float) -> Tuple[float, float]:
        x1, y1 = first
        x2, y2 = second
        if abs(y2 - y1) < 1e-15:
            return x1, y
        return x1 + (x2 - x1) * (y - y1) / (y2 - y1), y

    clip_edge(lambda point: point[0] >= min_x, lambda first, second: at_x(first, second, min_x))
    clip_edge(lambda point: point[0] <= max_x, lambda first, second: at_x(first, second, max_x))
    clip_edge(lambda point: point[1] >= min_y, lambda first, second: at_y(first, second, min_y))
    clip_edge(lambda point: point[1] <= max_y, lambda first, second: at_y(first, second, max_y))

    deduplicated: List[Tuple[float, float]] = []
    for point in polygon:
        if not deduplicated or any(abs(a - b) > 1e-10 for a, b in zip(point, deduplicated[-1])):
            deduplicated.append(point)
    if len(deduplicated) > 1 and all(
        abs(a - b) < 1e-10 for a, b in zip(deduplicated[0], deduplicated[-1])
    ):
        deduplicated.pop()
    return deduplicated


def poly_area_signed(pts: List[Tuple[float, float]]) -> float:
    """Signed area (shoelace). Positive = CCW in the *current coordinate system*."""
    if len(pts) < 3:
        return 0.0
    x0, y0 = pts[0]
    poly = pts if pts[-1] == pts[0] else (pts + [(x0, y0)])
    a = 0.0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:]):
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def rotate_points_mm(
    pts: List[Tuple[float, float]],
    deg_ccw: float,
    center: Tuple[float, float],
) -> List[Tuple[float, float]]:
    if abs(deg_ccw) < 1e-12:
        return pts
    cx, cy = center
    th = math.radians(deg_ccw)
    c = math.cos(th)
    s = math.sin(th)
    out: List[Tuple[float, float]] = []
    for x, y in pts:
        x0 = x - cx
        y0 = y - cy
        xr = x0 * c - y0 * s
        yr = x0 * s + y0 * c
        out.append((xr + cx, yr + cy))
    return out


def map_user_points_to_local_mm(
    pts_user: List[Tuple[float, float]],
    viewbox: Tuple[float, float, float, float],
    mapping: ViewBoxMapping,
    # fixed “working” behaviour:
    flip_svg_y: bool = True,
    svg_anchor_top_left: bool = True,
) -> List[Tuple[float, float]]:
    """
    Convert from SVG user units to local mm coordinates.

    We preserve absolute positions by anchoring to viewBox min.
    Our current working orientation uses:
      - x: right positive
      - y: upwards positive in local (by applying a flip)
    With svg_anchor_top_left=True, the SVG top-left is (0,0) and y goes negative downward.
    """
    vb_minx, vb_miny, _vb_w, _vb_h = viewbox
    out: List[Tuple[float, float]] = []
    for x_u, y_u in pts_user:
        x0_u = x_u - vb_minx
        y0_u = y_u - vb_miny
        x_mm = x0_u * mapping.sx + mapping.offset_x_mm
        y_mm = y0_u * mapping.sy + mapping.offset_y_mm

        if flip_svg_y:
            if svg_anchor_top_left:
                y_mm = -y_mm
            else:
                # not used in this simplified script
                pass

        out.append((x_mm, y_mm))
    return out


def point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """Even/odd point containment for non-self-intersecting sampled contours."""
    x, y = point
    inside = False
    if len(polygon) < 3:
        return False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            x_cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_cross:
                inside = not inside
        previous = current
    return inside


def classify_contour_polarities(
    polygons: List[List[Tuple[float, float]]],
    areas: List[float],
    fill_rule: str,
) -> List[Tuple[int, int, str]]:
    """Return (nesting depth, contour index, dark|clear) in safe emission order."""
    parents: List[Optional[int]] = [None] * len(polygons)
    for index, polygon in enumerate(polygons):
        candidates = [
            other
            for other, other_polygon in enumerate(polygons)
            if other != index
            and abs(areas[other]) > abs(areas[index])
            and point_in_polygon(polygon[0], other_polygon)
        ]
        if candidates:
            parents[index] = min(candidates, key=lambda candidate: abs(areas[candidate]))

    depths: List[int] = [0] * len(polygons)
    for index in range(len(polygons)):
        seen = set()
        parent = parents[index]
        while parent is not None:
            if parent in seen:
                raise ValueError("SVG contour nesting cycle detected")
            seen.add(parent)
            depths[index] += 1
            parent = parents[parent]

    polarities: Dict[int, str] = {}
    for index, area in enumerate(areas):
        if fill_rule == "evenodd":
            polarity = "dark" if depths[index] % 2 == 0 else "clear"
        else:
            ancestor_winding = 0
            parent = parents[index]
            while parent is not None:
                ancestor_winding += 1 if areas[parent] >= 0 else -1
                parent = parents[parent]
            inside_winding = ancestor_winding + (1 if area >= 0 else -1)
            outside_filled = ancestor_winding != 0
            inside_filled = inside_winding != 0
            if outside_filled == inside_filled:
                continue
            polarity = "dark" if inside_filled else "clear"
        polarities[index] = polarity

    # Emit each parent immediately followed by its descendants. Some Gerber
    # consumers composite clear polarity sequentially, so grouping every dark
    # contour before every hole can produce an incorrect layer even when the
    # final set-theoretic geometry looks equivalent.
    children: Dict[Optional[int], List[int]] = {}
    for index, parent in enumerate(parents):
        children.setdefault(parent, []).append(index)
    classified: List[Tuple[int, int, str]] = []

    def append_subtree(index: int) -> None:
        if index in polarities:
            classified.append((depths[index], index, polarities[index]))
        for child in children.get(index, []):
            append_subtree(child)

    for root_index in children.get(None, []):
        append_subtree(root_index)
    return classified


# ----------------------------
# Gerber emission (regions)
# ----------------------------

def region_from_polygon_mm(fmt: GerberFormat, poly_mm: List[Tuple[float, float]], place_mm: Tuple[float, float]) -> str:
    if len(poly_mm) < 3:
        return ""

    px, py = place_mm
    pts = [(x + px, y + py) for (x, y) in poly_mm]

    # close polygon
    if abs(pts[0][0] - pts[-1][0]) > 1e-6 or abs(pts[0][1] - pts[-1][1]) > 1e-6:
        pts.append(pts[0])

    out: List[str] = []
    out.append("G36*")

    x0_u = fmt.mm_to_units(pts[0][0])
    y0_u = fmt.mm_to_units(pts[0][1])
    out.append(f"X{fmt.fmt_coord(x0_u)}Y{fmt.fmt_coord(y0_u)}D02*")

    for x_mm, y_mm in pts[1:]:
        xu = fmt.mm_to_units(x_mm)
        yu = fmt.mm_to_units(y_mm)
        out.append(f"X{fmt.fmt_coord(xu)}Y{fmt.fmt_coord(yu)}D01*")

    out.append("G37*")
    return "\n".join(out) + "\n"


def build_one_svg_snippet(
    fmt: GerberFormat,
    svg_path: Path,
    place_mm: Tuple[float, float],
    rotate_deg_ccw: float,
    default_svg_size_mm: Optional[Tuple[float, float]],
    expected_svg_size_mm: Optional[Tuple[float, float]] = None,
    tolerance_mm: float = 0.05,
    invert_board_y: bool = True,
) -> str:
    """
    Convert one SVG into Gerber region statements at a single placement.
    - Parent transforms, visibility, basic shapes and fill rules are resolved first.
    - Artwork outside the SVG viewport is clipped, matching normal SVG rendering.
    - Rotation is about the centre of the SVG physical size.
    """
    viewbox = read_svg_viewbox(svg_path)
    physical_mm = read_svg_physical_size_mm(svg_path, fallback=default_svg_size_mm)
    if expected_svg_size_mm is not None and any(
        abs(actual - expected) > 0.01
        for actual, expected in zip(physical_mm, expected_svg_size_mm)
    ):
        raise ValueError(
            f"{svg_path.name}: physical size is {physical_mm[0]:g}x{physical_mm[1]:g}mm; "
            f"this job requires {expected_svg_size_mm[0]:g}x{expected_svg_size_mm[1]:g}mm"
        )
    root, shapes = load_svg_shapes(svg_path)
    mapping = compute_viewbox_mapping(
        viewbox,
        physical_mm,
        root.attrib.get("preserveAspectRatio", "xMidYMid meet"),
    )

    # Convert tolerance in mm to step in SVG user units (approx; using sx)
    tol_user = max(0.01, tolerance_mm / min(mapping.sx, mapping.sy))
    vb_minx, vb_miny, vb_w, vb_h = viewbox

    # Local centre in our "top-left anchored, y-up" coordinates:
    w_mm, h_mm = physical_mm
    center_local = (w_mm / 2.0, -h_mm / 2.0)

    # Placement mapping (KiCad-style coords) -> our Gerber coords
    px, py = place_mm
    if invert_board_y:
        py = -py
    place = (px, py)

    out: List[str] = []
    emitted_regions = 0
    for shape in shapes:
        subpaths = shape.path.continuous_subpaths()
        polys_local_mm: List[List[Tuple[float, float]]] = []
        areas: List[float] = []

        for sp in subpaths:
            pts_user = sample_subpath_to_points(sp, step_user_units=tol_user)
            pts_user = clip_polygon_to_rect(
                pts_user,
                vb_minx,
                vb_miny,
                vb_minx + vb_w,
                vb_miny + vb_h,
            )
            if len(pts_user) < 3:
                continue

            poly_mm = map_user_points_to_local_mm(
                pts_user,
                viewbox=viewbox,
                mapping=mapping,
                flip_svg_y=True,
                svg_anchor_top_left=True,
            )
            # `slice` and non-uniform mappings can extend viewBox content beyond
            # the physical viewport. SVG renderers clip that content, so do the same.
            poly_mm = clip_polygon_to_rect(poly_mm, 0.0, -h_mm, w_mm, 0.0)
            if len(poly_mm) < 3:
                continue

            # rotate around SVG centre (in local mm coords)
            poly_mm = rotate_points_mm(poly_mm, deg_ccw=rotate_deg_ccw, center=center_local)

            a = poly_area_signed(poly_mm)
            if abs(a) < 1e-12:
                continue
            polys_local_mm.append(poly_mm)
            areas.append(a)

        if not polys_local_mm:
            continue

        for _depth, contour_index, polarity in classify_contour_polarities(
            polys_local_mm,
            areas,
            shape.fill_rule,
        ):
            out.append("%LPD*%" if polarity == "dark" else "%LPC*%")
            out.append(region_from_polygon_mm(fmt, polys_local_mm[contour_index], place))
            emitted_regions += 1

        out.append("%LPD*%")  # restore

    if emitted_regions == 0:
        raise ValueError(
            f"{svg_path.name}: no filled artwork remains inside the SVG canvas. "
            "Move or resize the artwork so it overlaps the page."
        )
    return "".join(out)


def build_all_snippet(
    fmt: GerberFormat,
    items: List[Tuple[Path, Tuple[float, float], float]],
    default_svg_size_mm: Optional[Tuple[float, float]],
    expected_svg_size_mm: Optional[Tuple[float, float]] = None,
) -> str:
    # tiny aperture to keep some viewers/tools happy even if only regions are used
    ap_mm = 0.10
    ap_units = fmt.mm_to_units(ap_mm)
    ap_str = f"{ap_units:.6f}".rstrip("0").rstrip(".")

    out: List[str] = []
    out.append("G04 ---- SVG ARTWORK APPEND START ----*")
    out.append("%LPD*%")
    out.append(f"%ADD99C,{ap_str}*%")
    out.append("D99*")
    out.append("G01*")

    for svg_path, place, rot in items:
        out.append(f"G04 ---- SVG: {svg_path.name} @ {place[0]:.3f},{place[1]:.3f} rot {rot:.1f} ----*")
        out.append(build_one_svg_snippet(
            fmt=fmt,
            svg_path=svg_path,
            place_mm=place,
            rotate_deg_ccw=rot,
            default_svg_size_mm=default_svg_size_mm,
            expected_svg_size_mm=expected_svg_size_mm,
            tolerance_mm=0.05,
            invert_board_y=True,
        ))

    out.append("G04 ---- SVG ARTWORK APPEND END ----*")
    return "\n".join(out) + "\n"


# ----------------------------
# CLI
# ----------------------------

def parse_xy(s: str) -> Tuple[float, float]:
    try:
        xs, ys = s.split(",")
        return float(xs), float(ys)
    except Exception:
        raise argparse.ArgumentTypeError("Expected X,Y (mm), e.g. 67.5,31.0")


def parse_size_mm(s: str) -> Tuple[float, float]:
    try:
        xs, ys = s.split(",")
        return float(xs), float(ys)
    except Exception:
        raise argparse.ArgumentTypeError("Expected W,H (mm), e.g. 19,11")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Append SVG artwork (as regions) into an existing KiCad front mask Gerber (.gts)."
    )
    ap.add_argument("--base-gts", required=True, type=Path, help="Input Gerber to append into (e.g. 4-up-blank-F_Mask.gts)")
    ap.add_argument("--out-gts", required=True, type=Path, help="Output Gerber filename")
    ap.add_argument("--default-svg-size-mm", type=parse_size_mm, default=None,
                    help="Fallback SVG physical size (W,H) if an SVG lacks width/height. Optional.")
    ap.add_argument("--expected-svg-size-mm", type=parse_size_mm, default=None,
                    help="Reject SVGs whose resolved physical size is not W,H millimetres. Optional.")

    ap.add_argument("--svg", action="append", default=[], type=Path,
                    help="SVG file to place. Repeatable; must match count of --place and --rotate.")
    ap.add_argument("--place", action="append", default=[], type=parse_xy,
                    help="Placement X,Y in mm (KiCad/GerbView coordinates). Repeatable; matches --svg by index.")
    ap.add_argument("--rotate", action="append", default=[], type=float,
                    help="Rotation in degrees CCW around the centre of the SVG. Repeatable; matches --svg by index.")

    args = ap.parse_args()

    if not args.svg:
        raise SystemExit("You must provide at least one --svg")
    if len(args.svg) != len(args.place) or len(args.svg) != len(args.rotate):
        raise SystemExit(
            f"Counts must match: got --svg={len(args.svg)} --place={len(args.place)} --rotate={len(args.rotate)}"
        )

    base_text = args.base_gts.read_text(encoding="utf-8", errors="replace")
    fmt = parse_gerber_format(base_text)

    items: List[Tuple[Path, Tuple[float, float], float]] = []
    for svg_path, place, rot in zip(args.svg, args.place, args.rotate):
        if not svg_path.exists():
            raise SystemExit(f"SVG not found: {svg_path}")
        items.append((svg_path, place, rot))

    snippet = build_all_snippet(
        fmt,
        items,
        default_svg_size_mm=args.default_svg_size_mm,
        expected_svg_size_mm=args.expected_svg_size_mm,
    )

    insert_at = find_m02_insert_index(base_text)
    out_text = base_text[:insert_at] + "\n" + snippet + "\n" + base_text[insert_at:]
    args.out_gts.write_text(out_text, encoding="utf-8")

    print(f"Wrote {args.out_gts} (inserted artwork before M02*)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ET.ParseError, OSError, ValueError) as exc:
        print(f"Artwork error: {exc}", file=sys.stderr)
        raise SystemExit(2)
