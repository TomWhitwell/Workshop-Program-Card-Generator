# =============================================================================
# append_svg_to_mask_gts.py
#
# PURPOSE
# -------
# Append one or more pre-made SVG “artwork” files into an existing KiCad Gerber
# solder mask layer (typically Front Mask: *.gts), writing a new *.gts output.
#
# This is designed for a low-user workflow where non-electrical artwork is
# authored in Illustrator/Inkscape/InDesign, exported as SVG to fixed specs,
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
#    - SVG paths are read via svgpathtools.
#    - Each SVG <path> element may contain multiple “subpaths” (compound shapes).
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
#    - The tricky bit: different tools may choose different winding directions
#      for outer/inner shapes, especially after Y-flips.
#    - To make this robust, we do NOT assume “CCW = filled”.
#      Instead, for each original SVG <path> element we:
#        a) Compute signed area for each subpath polygon
#        b) Pick the subpath with the largest absolute area as the “outer”
#        c) Whatever winding sign that “outer” uses becomes DARK for that <path>
#        d) Any subpath with the opposite sign becomes CLEAR (a hole)
#
# 4) Coordinate mapping:
#    - SVG uses a Y-down coordinate system; KiCad/Gerber viewers commonly use
#      Y-up world coordinates.
#    - We map SVG -> “local mm” using:
#        - viewBox origin as the reference (origin mode: viewBox)
#        - scale derived from SVG physical size (width/height) vs viewBox size
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
# 3) Artwork should be “filled shapes”, not strokes where possible.
#    - Strokes can be converted to outlines in the design tool if needed.
#    - This script treats paths as filled regions.
#
# 4) Fonts should be outlined/converted to paths in the design tool.
#    - Do not rely on runtime font availability.
#
# 5) No electrical relevance:
#    - This is intended for solder mask art. It does not check clearances,
#      min feature sizes, polarity rules, or manufacturability.
#    - You are responsible for the SVG creation rules and any DFM constraints.
#
#
# TROUBLESHOOTING
# ---------------
# - “Letters collapse into a blob / overlapping block”
#     That happens if you rebase each path to its own bbox. This script uses
#     the SVG viewBox origin to preserve absolute positions.
#
# - “Holes in 0/A/R are wrong”
#     This is handled by the “largest absolute area is outer contour” rule per
#     SVG <path>. If you still see issues, check that your exporter produces
#     compound paths (outer + inner) rather than separate independent fills.
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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

from svgpathtools import svg2paths2  # type: ignore
from svgpathtools.path import Path as SvgPathT  # type: ignore


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
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)([a-zA-Z%]*)$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()

    # SVG/CSS absolute units; assume 96 dpi for px
    if unit in ("mm", ""):
        return num
    if unit == "cm":
        return num * 10.0
    if unit == "in":
        return num * 25.4
    if unit == "pt":  # 72 pt per inch
        return num * (25.4 / 72.0)
    if unit == "pc":  # 6 pc per inch
        return num * (25.4 / 6.0)
    if unit == "px":  # 96 px per inch
        return num * (25.4 / 96.0)

    return None


def read_svg_root(svg_path: Path) -> ET.Element:
    txt = svg_path.read_text(encoding="utf-8", errors="replace")
    return ET.fromstring(txt)


def read_svg_viewbox(svg_path: Path) -> Tuple[float, float, float, float]:
    root = read_svg_root(svg_path)
    vb = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if not vb:
        raise ValueError(f"{svg_path}: missing viewBox (required for reliable scaling)")
    parts = [float(x) for x in re.split(r"[,\s]+", vb.strip()) if x]
    if len(parts) != 4:
        raise ValueError(f"{svg_path}: malformed viewBox='{vb}'")
    return parts[0], parts[1], parts[2], parts[3]


def read_svg_physical_size_mm(svg_path: Path, fallback: Optional[Tuple[float, float]]) -> Tuple[float, float]:
    root = read_svg_root(svg_path)
    w_mm = parse_svg_length_to_mm(root.attrib.get("width", ""))
    h_mm = parse_svg_length_to_mm(root.attrib.get("height", ""))
    if w_mm is not None and h_mm is not None:
        return float(w_mm), float(h_mm)

    if fallback is not None:
        return fallback

    raise ValueError(
        f"{svg_path}: missing width/height (or unparseable units). "
        "Either ensure width/height are set (e.g. '19mm') or pass --default-svg-size-mm W,H."
    )


def compute_user_to_mm_scale(
    viewbox: Tuple[float, float, float, float],
    physical_mm: Tuple[float, float],
) -> Tuple[float, float]:
    _minx, _miny, vb_w, vb_h = viewbox
    if vb_w == 0 or vb_h == 0:
        raise ValueError("SVG viewBox has zero size")
    w_mm, h_mm = physical_mm
    return w_mm / vb_w, h_mm / vb_h


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
    scale_xy: Tuple[float, float],
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
    sx, sy = scale_xy

    out: List[Tuple[float, float]] = []
    for x_u, y_u in pts_user:
        x0_u = x_u - vb_minx
        y0_u = y_u - vb_miny
        x_mm = x0_u * sx
        y_mm = y0_u * sy

        if flip_svg_y:
            if svg_anchor_top_left:
                y_mm = -y_mm
            else:
                # not used in this simplified script
                pass

        out.append((x_mm, y_mm))
    return out


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
    tolerance_mm: float = 0.05,
    invert_board_y: bool = True,
) -> str:
    """
    Convert one SVG into Gerber region statements at a single placement.
    - Holes/counters handled with: outer contour = largest |area| for each original <path>.
    - Rotation is about the centre of the SVG physical size.
    """
    viewbox = read_svg_viewbox(svg_path)
    physical_mm = read_svg_physical_size_mm(svg_path, fallback=default_svg_size_mm)
    sx, sy = compute_user_to_mm_scale(viewbox, physical_mm)

    # Convert tolerance in mm to step in SVG user units (approx; using sx)
    tol_user = max(0.01, tolerance_mm / sx)

    paths, _attrs, _svg_attrs = svg2paths2(str(svg_path))
    if not paths:
        return ""

    # Local centre in our "top-left anchored, y-up" coordinates:
    w_mm, h_mm = physical_mm
    center_local = (w_mm / 2.0, -h_mm / 2.0)

    # Placement mapping (KiCad-style coords) -> our Gerber coords
    px, py = place_mm
    if invert_board_y:
        py = -py
    place = (px, py)

    out: List[str] = []
    for p in paths:
        subpaths = p.continuous_subpaths()
        polys_local_mm: List[List[Tuple[float, float]]] = []
        areas: List[float] = []

        for sp in subpaths:
            pts_user = sample_subpath_to_points(sp, step_user_units=tol_user)
            if len(pts_user) < 3:
                continue

            poly_mm = map_user_points_to_local_mm(
                pts_user,
                viewbox=viewbox,
                scale_xy=(sx, sy),
                flip_svg_y=True,
                svg_anchor_top_left=True,
            )

            # rotate around SVG centre (in local mm coords)
            poly_mm = rotate_points_mm(poly_mm, deg_ccw=rotate_deg_ccw, center=center_local)

            a = poly_area_signed(poly_mm)
            polys_local_mm.append(poly_mm)
            areas.append(a)

        if not polys_local_mm:
            continue

        # OUTER winding sign: choose the contour with largest absolute area
        outer_idx = max(range(len(areas)), key=lambda i: abs(areas[i]))
        dark_sign = +1 if areas[outer_idx] >= 0 else -1

        for poly_mm, a in zip(polys_local_mm, areas):
            s = +1 if a >= 0 else -1
            if s == dark_sign:
                out.append("%LPD*%")
            else:
                out.append("%LPC*%")
            out.append(region_from_polygon_mm(fmt, poly_mm, place))

        out.append("%LPD*%")  # restore

    return "".join(out)


def build_all_snippet(
    fmt: GerberFormat,
    items: List[Tuple[Path, Tuple[float, float], float]],
    default_svg_size_mm: Optional[Tuple[float, float]],
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

    snippet = build_all_snippet(fmt, items, default_svg_size_mm=args.default_svg_size_mm)

    insert_at = find_m02_insert_index(base_text)
    out_text = base_text[:insert_at] + "\n" + snippet + "\n" + base_text[insert_at:]
    args.out_gts.write_text(out_text, encoding="utf-8")

    print(f"Wrote {args.out_gts} (inserted artwork before M02*)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
