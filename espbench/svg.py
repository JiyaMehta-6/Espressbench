import math
from xml.sax.saxutils import escape

WIDTH = 640
HEIGHT = 320
LEFT = 56
RIGHT = 16
TOP = 44
BOTTOM = 48
BAR_FILL = "#2f6fed"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _fmt(value):
    return f"{value:.4g}"


BADGE_COLORS = {
    "brightgreen": "#4c1",
    "green": "#97ca00",
    "blue": "#007ec6",
    "red": "#e05d44",
    "yellow": "#dfb317",
    "orange": "#fe7d37",
    "lightgrey": "#9f9f9f",
    "gray": "#555555",
    "grey": "#555555",
}


def badge_color(name):
    color = BADGE_COLORS.get(str(name).lower(), str(name))
    hex_part = color[1:] if color.startswith("#") else ""
    if not hex_part or any(c not in "0123456789abcdefABCDEF" for c in hex_part) \
            or len(hex_part) not in (3, 4, 6, 8):
        raise ValueError(f"unknown badge color {name!r}; use a shields color "
                         "name (brightgreen, red, blue, ...) or a hex value")
    return color


def badge_svg(label, value, color="blue"):
    fill = badge_color(color)
    label = str(label)
    value = str(value)
    label_w = max(7 * len(label) + 10, 14)
    value_w = max(7 * len(value) + 10, 14)
    total = label_w + value_w
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'font-family="Segoe UI, Arial, sans-serif" font-size="11" '
        f'text-anchor="middle">\n'
        f'  <clipPath id="badge-clip"><rect width="{total}" height="20" rx="3"/>'
        f'</clipPath>\n'
        f'  <g clip-path="url(#badge-clip)">\n'
        f'    <rect width="{total}" height="20" fill="{fill}"/>\n'
        f'    <rect width="{label_w}" height="20" fill="#555555"/>\n'
        f'  </g>\n'
        f'  <text x="{label_w / 2:.1f}" y="14" fill="#ffffff">{escape(label)}</text>\n'
        f'  <text x="{label_w + value_w / 2:.1f}" y="14" fill="#ffffff">'
        f'{escape(value)}</text>\n'
        f'</svg>'
    )


def _frame(body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'font-family="Segoe UI, Arial, sans-serif">\n'
        + "\n".join(body)
        + "\n</svg>"
    )


def _text(x, y, content, size=12, anchor="start", fill="#222222", bold=False):
    weight = ' font-weight="bold"' if bold else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'font-size="{size}"{weight} fill="{fill}">{escape(str(content))}</text>')


def histogram_svg(values, title="histogram", unit=""):
    numbers = []
    for value in values or []:
        number = _finite(value)
        if number is not None:
            numbers.append(number)
    if not numbers:
        return _frame([
            _text(WIDTH / 2, 26, title, size=16, anchor="middle", bold=True),
            _text(WIDTH / 2, HEIGHT / 2, "no data", size=14, anchor="middle",
                  fill="#777777"),
        ])
    plot_w = WIDTH - LEFT - RIGHT
    plot_h = HEIGHT - TOP - BOTTOM
    low, high = min(numbers), max(numbers)
    span = high - low
    if span <= 0 or len(numbers) == 1:
        counts = [len(numbers)]
    else:
        bins = max(1, min(30, math.ceil(math.log2(len(numbers))) + 1))
        counts = [0] * bins
        for number in numbers:
            index = int((number - low) / span * bins)
            counts[min(index, bins - 1)] += 1
    peak = max(counts) or 1
    bin_w = plot_w / len(counts)
    body = [
        _text(WIDTH / 2, 26, title, size=16, anchor="middle", bold=True),
        f'<line x1="{LEFT}" y1="{TOP + plot_h}" x2="{LEFT + plot_w}" '
        f'y2="{TOP + plot_h}" stroke="#333333" stroke-width="1"/>',
        f'<line x1="{LEFT}" y1="{TOP}" x2="{LEFT}" y2="{TOP + plot_h}" '
        f'stroke="#333333" stroke-width="1"/>',
    ]
    for index, count in enumerate(counts):
        bar_h = plot_h * (count / peak)
        x = LEFT + index * bin_w
        y = TOP + plot_h - bar_h
        body.append(
            f'<rect x="{x + 1:.2f}" y="{y:.2f}" '
            f'width="{max(bin_w - 2, 1):.2f}" height="{bar_h:.2f}" '
            f'fill="{BAR_FILL}"/>')
    body.append(_text(LEFT - 8, TOP + 12, peak, size=11, anchor="end"))
    body.append(_text(LEFT - 8, TOP + plot_h + 4, 0, size=11, anchor="end"))
    body.append(_text(LEFT, TOP + plot_h + 20, _fmt(low), size=11))
    body.append(_text(LEFT + plot_w, TOP + plot_h + 20, _fmt(high),
                      size=11, anchor="end"))
    axis = f"{unit} (n={len(numbers)})" if unit else f"n={len(numbers)}"
    body.append(_text(WIDTH / 2, HEIGHT - 10, axis, size=12, anchor="middle",
                      fill="#444444"))
    return _frame(body)
