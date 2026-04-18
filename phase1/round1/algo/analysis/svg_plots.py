from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable


PALETTE = [
    "#0f766e",
    "#c2410c",
    "#2563eb",
    "#be123c",
    "#7c3aed",
    "#15803d",
]
NEUTRAL_BAR = "#0f766e"
BG = "#f8fafc"
PANEL = "#ffffff"
TEXT = "#0f172a"
MUTED = "#475569"
GRID = "#dbe4ee"
FRAME = "#94a3b8"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _svg_header(width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{BG}"/>',
        "<style>",
        f"text {{ font-family: Avenir Next, Helvetica, Arial, sans-serif; fill: {TEXT}; }}",
        ".title { font-size: 22px; font-weight: 700; }",
        f".subtitle {{ font-size: 12px; fill: {MUTED}; }}",
        ".axis { font-size: 12px; }",
        ".legend { font-size: 12px; }",
        f".grid {{ stroke: {GRID}; stroke-width: 1; }}",
        f".frame {{ stroke: {FRAME}; fill: {PANEL}; stroke-width: 1; rx: 10; }}",
        "</style>",
    ]


def _svg_footer(lines: list[str], path: Path) -> None:
    lines.append("</svg>")
    _ensure_parent(path)
    path.write_text("\n".join(lines))


def _finite_pairs(xs: Iterable[float], ys: Iterable[float]) -> list[tuple[float, float]]:
    pairs = []
    for x, y in zip(xs, ys):
        if x is None or y is None:
            continue
        if isinstance(x, float) and not math.isfinite(x):
            continue
        if isinstance(y, float) and not math.isfinite(y):
            continue
        pairs.append((float(x), float(y)))
    return pairs


def _format_tick(value: float) -> str:
    if abs(value) >= 100000:
        return f"{value/1000:.0f}k"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:.0f}"
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _downsample_points(points: list[tuple[float, float]], max_points: int = 900) -> list[tuple[float, float]]:
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def line_chart(
    path: Path,
    title: str,
    series: list[dict],
    x_label: str,
    y_label: str,
    width: int = 1200,
    height: int = 700,
) -> None:
    lines = _svg_header(width, height)
    margin = {"left": 96, "right": 28, "top": 78, "bottom": 74}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    all_points: list[tuple[float, float]] = []
    cleaned = []
    for idx, item in enumerate(series):
        pairs = _finite_pairs(item.get("x", []), item.get("y", []))
        if not pairs:
            continue
        cleaned.append(
            {
                "label": item.get("label", f"series_{idx}"),
                "color": item.get("color", PALETTE[idx % len(PALETTE)]),
                "points": _downsample_points(pairs),
            }
        )
        all_points.extend(pairs)
    if not cleaned:
        cleaned = [{"label": "empty", "color": PALETTE[0], "points": [(0.0, 0.0), (1.0, 0.0)]}]
        all_points = cleaned[0]["points"]

    x_min = min(x for x, _ in all_points)
    x_max = max(x for x, _ in all_points)
    y_min = min(y for _, y in all_points)
    y_max = max(y for _, y in all_points)
    if x_min == x_max:
        x_max += 1.0
    if y_min == y_max:
        y_max += 1.0
    y_pad = (y_max - y_min) * 0.08
    y_min -= y_pad
    y_max += y_pad

    def sx(x: float) -> float:
        return margin["left"] + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return margin["top"] + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    lines.append(f'<text class="title" x="{margin["left"]}" y="38">{title}</text>')
    lines.append(f'<text class="subtitle" x="{margin["left"]}" y="58">Overlayed series with shared axes</text>')
    for i in range(6):
        gx = margin["left"] + plot_w * i / 5
        gy = margin["top"] + plot_h * i / 5
        lines.append(f'<line class="grid" x1="{gx:.1f}" y1="{margin["top"]}" x2="{gx:.1f}" y2="{margin["top"] + plot_h}"/>')
        lines.append(f'<line class="grid" x1="{margin["left"]}" y1="{gy:.1f}" x2="{margin["left"] + plot_w}" y2="{gy:.1f}"/>')
    lines.append(f'<rect class="frame" x="{margin["left"]}" y="{margin["top"]}" width="{plot_w}" height="{plot_h}"/>')

    for item in cleaned:
        points = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in item["points"])
        lines.append(
            f'<polyline fill="none" stroke="{item["color"]}" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" points="{points}"/>'
        )

    for i in range(6):
        x_tick = x_min + (x_max - x_min) * i / 5
        y_tick = y_min + (y_max - y_min) * (5 - i) / 5
        lines.append(
            f'<text class="axis" x="{margin["left"] + plot_w * i / 5:.1f}" y="{height - 30}" text-anchor="middle">{_format_tick(x_tick)}</text>'
        )
        lines.append(
            f'<text class="axis" x="{margin["left"] - 12}" y="{margin["top"] + plot_h * i / 5 + 4:.1f}" text-anchor="end">{_format_tick(y_tick)}</text>'
        )

    lines.append(f'<text class="axis" x="{margin["left"] + plot_w / 2:.1f}" y="{height - 8}" text-anchor="middle">{x_label}</text>')
    lines.append(
        f'<text class="axis" transform="translate(22 {margin["top"] + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle">{y_label}</text>'
    )

    legend_x = margin["left"] + 10
    legend_y = 70
    for idx, item in enumerate(cleaned):
        ly = legend_y + idx * 18
        lines.append(f'<line x1="{legend_x}" y1="{ly}" x2="{legend_x + 18}" y2="{ly}" stroke="{item["color"]}" stroke-width="3"/>')
        lines.append(f'<text class="legend" x="{legend_x + 24}" y="{ly + 4}">{item["label"]}</text>')

    _svg_footer(lines, path)


def histogram_chart(
    path: Path,
    title: str,
    series: list[dict],
    x_label: str,
    y_label: str = "Density",
    bins: int = 30,
    width: int = 1200,
    height: int = 700,
) -> None:
    lines = _svg_header(width, height)
    margin = {"left": 96, "right": 28, "top": 78, "bottom": 74}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    cleaned = []
    all_values: list[float] = []
    for idx, item in enumerate(series):
        values = [float(v) for v in item.get("values", []) if v is not None and math.isfinite(float(v))]
        if not values:
            continue
        cleaned.append(
            {
                "label": item.get("label", f"series_{idx}"),
                "color": item.get("color", PALETTE[idx % len(PALETTE)]),
                "values": values,
            }
        )
        all_values.extend(values)
    if not cleaned:
        cleaned = [{"label": "empty", "color": PALETTE[0], "values": [0.0]}]
        all_values = [0.0, 1.0]

    x_min = min(all_values)
    x_max = max(all_values)
    if x_min == x_max:
        x_max += 1.0
    bin_w = (x_max - x_min) / max(bins, 1)
    densities = []
    for item in cleaned:
        counts = [0] * bins
        for value in item["values"]:
            idx = min(bins - 1, max(0, int((value - x_min) / bin_w)))
            counts[idx] += 1
        total = max(1, len(item["values"]))
        dens = [count / total for count in counts]
        item["densities"] = dens
        densities.extend(dens)
    y_max = max(densities) if densities else 1.0
    y_max *= 1.15

    def sx(x: float) -> float:
        return margin["left"] + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return margin["top"] + plot_h - y / y_max * plot_h

    lines.append(f'<text class="title" x="{margin["left"]}" y="38">{title}</text>')
    lines.append(f'<text class="subtitle" x="{margin["left"]}" y="58">Distribution view</text>')
    for i in range(6):
        gx = margin["left"] + plot_w * i / 5
        gy = margin["top"] + plot_h * i / 5
        lines.append(f'<line class="grid" x1="{gx:.1f}" y1="{margin["top"]}" x2="{gx:.1f}" y2="{margin["top"] + plot_h}"/>')
        lines.append(f'<line class="grid" x1="{margin["left"]}" y1="{gy:.1f}" x2="{margin["left"] + plot_w}" y2="{gy:.1f}"/>')
    lines.append(f'<rect class="frame" x="{margin["left"]}" y="{margin["top"]}" width="{plot_w}" height="{plot_h}"/>')

    bar_group_w = plot_w / bins
    per_series_w = bar_group_w / max(1, len(cleaned))
    for s_idx, item in enumerate(cleaned):
        for idx, dens in enumerate(item["densities"]):
            x0 = margin["left"] + idx * bar_group_w + s_idx * per_series_w
            y0 = sy(dens)
            h = margin["top"] + plot_h - y0
            lines.append(
                f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{max(per_series_w - 2, 1):.2f}" height="{max(h, 0):.2f}" rx="2" '
                f'fill="{item["color"]}" fill-opacity="0.55"/>'
            )

    for i in range(6):
        x_tick = x_min + (x_max - x_min) * i / 5
        y_tick = y_max * (5 - i) / 5
        lines.append(
            f'<text class="axis" x="{margin["left"] + plot_w * i / 5:.1f}" y="{height - 30}" text-anchor="middle">{_format_tick(x_tick)}</text>'
        )
        lines.append(
            f'<text class="axis" x="{margin["left"] - 12}" y="{margin["top"] + plot_h * i / 5 + 4:.1f}" text-anchor="end">{_format_tick(y_tick)}</text>'
        )
    lines.append(f'<text class="axis" x="{margin["left"] + plot_w / 2:.1f}" y="{height - 8}" text-anchor="middle">{x_label}</text>')
    lines.append(
        f'<text class="axis" transform="translate(22 {margin["top"] + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle">{y_label}</text>'
    )

    legend_x = margin["left"] + 10
    legend_y = 70
    for idx, item in enumerate(cleaned):
        ly = legend_y + idx * 18
        lines.append(f'<rect x="{legend_x}" y="{ly - 8}" width="16" height="10" fill="{item["color"]}" fill-opacity="0.55"/>')
        lines.append(f'<text class="legend" x="{legend_x + 24}" y="{ly + 1}">{item["label"]}</text>')
    _svg_footer(lines, path)


def bar_chart(
    path: Path,
    title: str,
    labels: list[str],
    values: list[float],
    x_label: str,
    y_label: str,
    width: int = 1200,
    height: int = 700,
) -> None:
    lines = _svg_header(width, height)
    margin = {"left": 96, "right": 28, "top": 78, "bottom": 120}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    if not values:
        labels = ["empty"]
        values = [0.0]
    y_max = max(values) if max(values) > 0 else 1.0
    y_max *= 1.15
    bar_w = plot_w / max(len(values), 1)

    def sy(y: float) -> float:
        return margin["top"] + plot_h - y / y_max * plot_h

    lines.append(f'<text class="title" x="{margin["left"]}" y="38">{title}</text>')
    lines.append(f'<text class="subtitle" x="{margin["left"]}" y="58">Category comparison</text>')
    for i in range(6):
        gy = margin["top"] + plot_h * i / 5
        lines.append(f'<line class="grid" x1="{margin["left"]}" y1="{gy:.1f}" x2="{margin["left"] + plot_w}" y2="{gy:.1f}"/>')
    lines.append(f'<rect class="frame" x="{margin["left"]}" y="{margin["top"]}" width="{plot_w}" height="{plot_h}"/>')

    for idx, value in enumerate(values):
        x0 = margin["left"] + idx * bar_w + 4
        y0 = sy(value)
        h = margin["top"] + plot_h - y0
        lines.append(
            f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{max(bar_w - 10, 2):.2f}" height="{max(h, 0):.2f}" rx="3" fill="{NEUTRAL_BAR}" fill-opacity="0.82"/>'
        )
        if len(labels) <= 12:
            lines.append(
                f'<text class="axis" transform="translate({x0 + max(bar_w - 10, 2) / 2:.2f} {height - 42}) rotate(-28)" text-anchor="end">{labels[idx]}</text>'
            )
    for i in range(6):
        y_tick = y_max * (5 - i) / 5
        lines.append(
            f'<text class="axis" x="{margin["left"] - 12}" y="{margin["top"] + plot_h * i / 5 + 4:.1f}" text-anchor="end">{_format_tick(y_tick)}</text>'
        )
    lines.append(f'<text class="axis" x="{margin["left"] + plot_w / 2:.1f}" y="{height - 8}" text-anchor="middle">{x_label}</text>')
    lines.append(
        f'<text class="axis" transform="translate(22 {margin["top"] + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle">{y_label}</text>'
    )
    _svg_footer(lines, path)


def heatmap_chart(
    path: Path,
    title: str,
    matrix: list[list[float]],
    x_labels: list[str],
    y_labels: list[str],
    x_label: str,
    y_label: str,
    width: int = 1100,
    height: int = 700,
) -> None:
    lines = _svg_header(width, height)
    margin = {"left": 170, "right": 28, "top": 78, "bottom": 110}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if rows == 0 or cols == 0:
        matrix = [[0.0]]
        x_labels = ["empty"]
        y_labels = ["empty"]
        rows = cols = 1
    flat = [value for row in matrix for value in row]
    v_min = min(flat)
    v_max = max(flat)
    if v_min == v_max:
        v_max += 1.0
    cell_w = plot_w / cols
    cell_h = plot_h / rows

    def color(value: float) -> str:
        t = (value - v_min) / (v_max - v_min)
        r = int(247 - 153 * t)
        g = int(250 - 103 * t)
        b = int(252 - 199 * t)
        return f"rgb({max(r,0)},{max(g,0)},{max(b,0)})"

    lines.append(f'<text class="title" x="{margin["left"]}" y="38">{title}</text>')
    lines.append(f'<text class="subtitle" x="{margin["left"]}" y="58">Row-wise quote concentration across offset buckets</text>')
    lines.append(f'<rect class="frame" x="{margin["left"]}" y="{margin["top"]}" width="{plot_w}" height="{plot_h}"/>')
    for r in range(rows):
        for c in range(cols):
            x0 = margin["left"] + c * cell_w
            y0 = margin["top"] + r * cell_h
            value = matrix[r][c]
            lines.append(f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{cell_w:.2f}" height="{cell_h:.2f}" fill="{color(value)}"/>')
            if value >= (v_min + 0.18 * (v_max - v_min)):
                txt = "#ffffff" if value >= (v_min + 0.55 * (v_max - v_min)) else TEXT
                lines.append(
                    f'<text class="axis" x="{x0 + cell_w / 2:.2f}" y="{y0 + cell_h / 2 + 4:.2f}" text-anchor="middle" fill="{txt}">{value:.2f}</text>'
                )
    for c, label in enumerate(x_labels):
        x0 = margin["left"] + (c + 0.5) * cell_w
        lines.append(f'<text class="axis" transform="translate({x0:.2f} {height - 42}) rotate(-28)" text-anchor="end">{label}</text>')
    for r, label in enumerate(y_labels):
        y0 = margin["top"] + (r + 0.5) * cell_h + 4
        lines.append(f'<text class="axis" x="{margin["left"] - 10}" y="{y0:.2f}" text-anchor="end">{label}</text>')
    lines.append(f'<text class="axis" x="{margin["left"] + plot_w / 2:.1f}" y="{height - 8}" text-anchor="middle">{x_label}</text>')
    lines.append(
        f'<text class="axis" transform="translate(22 {margin["top"] + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle">{y_label}</text>'
    )
    _svg_footer(lines, path)


def ecdf_chart(
    path: Path,
    title: str,
    series: list[dict],
    x_label: str,
    y_label: str = "ECDF",
    width: int = 1200,
    height: int = 700,
) -> None:
    ecdf_series = []
    for idx, item in enumerate(series):
        values = sorted(float(v) for v in item.get("values", []) if v is not None and math.isfinite(float(v)))
        if not values:
            continue
        xs = values
        ys = [(i + 1) / len(values) for i in range(len(values))]
        ecdf_series.append(
            {
                "label": item.get("label", f"series_{idx}"),
                "color": item.get("color", PALETTE[idx % len(PALETTE)]),
                "x": xs,
                "y": ys,
            }
        )
    if not ecdf_series:
        ecdf_series = [{"label": "empty", "color": PALETTE[0], "x": [0.0, 1.0], "y": [0.0, 1.0]}]
    line_chart(path=path, title=title, series=ecdf_series, x_label=x_label, y_label=y_label, width=width, height=height)
