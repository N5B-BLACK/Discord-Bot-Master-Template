"""
Server-rendered SVG charts for the dashboard's Overview page (Phase 4).

Deliberately not using a JS charting library (Chart.js etc.) - these charts
are simple (a line and a bar chart, ~14 data points each) and rendering them
as a plain SVG string server-side means zero extra JS payload, nothing to
initialize client-side, and the chart is themeable with the same CSS custom
properties as everything else on the page.
"""


def _scale(values: list, height: int, top_pad: int = 10, bottom_pad: int = 10) -> list:
    """Maps values to y-coordinates (SVG y grows downward), handling the
    all-zero / single-value case so callers never divide by zero."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        # Flat data (including all-zero): draw a flat line/bars at mid-height
        # rather than collapsing everything onto the baseline, which would be
        # indistinguishable from "no data".
        mid = height - bottom_pad - (height - top_pad - bottom_pad) / 2
        return [mid for _ in values]
    usable = height - top_pad - bottom_pad
    return [height - bottom_pad - ((v - lo) / (hi - lo)) * usable for v in values]


def line_chart_svg(values: list, width: int = 600, height: int = 140, color: str = "#F0A94E") -> str:
    """A smooth-ish line chart (straight segments) with a soft fill under the
    line, for trend data like member count over time."""
    if not values:
        values = [0]
    n = len(values)
    ys = _scale(values, height)
    step = width / max(1, n - 1) if n > 1 else 0
    xs = [i * step for i in range(n)] if n > 1 else [width / 2]

    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    fill_path = f"M{xs[0]:.1f},{height} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + f" L{xs[-1]:.1f},{height} Z"

    dot = ""
    if n >= 1:
        dot = f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3.5" fill="{color}" />'

    return f"""<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" preserveAspectRatio="none" style="overflow:visible;">
    <defs>
        <linearGradient id="lineFade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="{color}" stop-opacity="0.25" />
            <stop offset="100%" stop-color="{color}" stop-opacity="0" />
        </linearGradient>
    </defs>
    <path d="{fill_path}" fill="url(#lineFade)" stroke="none" />
    <polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    {dot}
</svg>"""


def bar_chart_svg(values: list, width: int = 600, height: int = 140, color: str = "#9A87F0") -> str:
    """A simple bar chart for per-day counts like messages sent."""
    if not values:
        values = [0]
    n = len(values)
    hi = max(values) or 1
    gap = 4
    bar_w = max(2.0, (width - gap * (n - 1)) / n) if n > 0 else width
    usable_h = height - 8

    bars = ""
    for i, v in enumerate(values):
        bar_h = max(2.0, (v / hi) * usable_h) if hi else 2.0
        x = i * (bar_w + gap)
        y = height - bar_h
        opacity = 0.35 + 0.65 * (v / hi) if hi else 0.35
        bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="2" fill="{color}" opacity="{opacity:.2f}" />'

    return f"""<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" preserveAspectRatio="none" style="overflow:visible;">
    {bars}
</svg>"""
