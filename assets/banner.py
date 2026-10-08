"""Draw assets/banner.svg, the README's banner: FIGARO in the ANSI Shadow figlet font, in Figma's colours.

    python3 assets/banner.py

Every character cell becomes a shape, so the banner looks the same in every browser and needs no font: a
full block is a filled cell, and a box-drawing character is its pair of thin lines. The colour runs through
the Figma logo's palette from the first column to the last, one colour per column, as in a terminal.
"""
from pathlib import Path

ART = [
    "███████╗██╗ ██████╗  █████╗ ██████╗  ██████╗ ",
    "██╔════╝██║██╔════╝ ██╔══██╗██╔══██╗██╔═══██╗",
    "█████╗  ██║██║  ███╗███████║██████╔╝██║   ██║",
    "██╔══╝  ██║██║   ██║██╔══██║██╔══██╗██║   ██║",
    "██║     ██║╚██████╔╝██║  ██║██║  ██║╚██████╔╝",
    "╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ",
]
PALETTE = ["#F24E1E", "#FF7262", "#A259FF", "#1ABCFE", "#0ACF83"]
BACKGROUND = "#1E1E1E"
WIDTH, HEIGHT, RADIUS = 900, 210, 12
CELL_W, CELL_H = 12, 26
STROKE = 1.6  # the width of a box-drawing line
GAP = 2.4     # how far each line of a double line sits from the cell's middle


def mix(stops, t):
    """The colour at t (0…1) along evenly spaced stops."""
    t *= len(stops) - 1
    i = min(int(t), len(stops) - 2)
    a, b = (tuple(int(s[k:k + 2], 16) for k in (1, 3, 5)) for s in stops[i:i + 2])
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * (t - i)) for x, y in zip(a, b))


def lines(ch, x, y):
    """The strokes of one box-drawing character in the cell at x, y."""
    l, r, t, b = x, x + CELL_W, y, y + CELL_H
    mx, my = x + CELL_W / 2, y + CELL_H / 2
    lo, hi = my - GAP, my + GAP
    return {
        "═": [f"M{l:g} {lo:g}H{r:g}", f"M{l:g} {hi:g}H{r:g}"],
        "║": [f"M{mx - GAP:g} {t:g}V{b:g}", f"M{mx + GAP:g} {t:g}V{b:g}"],
        "╗": [f"M{l:g} {lo:g}H{mx + GAP:g}V{b:g}", f"M{l:g} {hi:g}H{mx - GAP:g}V{b:g}"],
        "╔": [f"M{r:g} {lo:g}H{mx - GAP:g}V{b:g}", f"M{r:g} {hi:g}H{mx + GAP:g}V{b:g}"],
        "╝": [f"M{l:g} {hi:g}H{mx + GAP:g}V{t:g}", f"M{l:g} {lo:g}H{mx - GAP:g}V{t:g}"],
        "╚": [f"M{r:g} {hi:g}H{mx - GAP:g}V{t:g}", f"M{r:g} {lo:g}H{mx + GAP:g}V{t:g}"],
    }[ch]


def banner():
    cols = max(len(row) for row in ART)
    x0, y0 = (WIDTH - cols * CELL_W) / 2, (HEIGHT - len(ART) * CELL_H) / 2
    blocks, strokes = {}, {}
    for r, row in enumerate(ART):
        for c, ch in enumerate(row):
            if ch == " ":
                continue
            colour = mix(PALETTE, c / (cols - 1))
            x, y = x0 + c * CELL_W, y0 + r * CELL_H
            if ch == "█":
                blocks.setdefault(colour, []).append(f"M{x:g} {y:g}h{CELL_W}v{CELL_H}h-{CELL_W}z")
            else:
                strokes.setdefault(colour, []).extend(lines(ch, x, y))
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" '
        f'height="{HEIGHT}" role="img" aria-label="Figaro">',
        "<title>Figaro</title>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="{RADIUS}" fill="{BACKGROUND}"/>',
        '<g shape-rendering="crispEdges">',
        *(f'<path fill="{c}" d="{"".join(d)}"/>' for c, d in blocks.items()),
        "</g>",
        f'<g fill="none" stroke-width="{STROKE}" stroke-linecap="square">',
        *(f'<path stroke="{c}" d="{"".join(d)}"/>' for c, d in strokes.items()),
        "</g>",
        "</svg>",
    ]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    Path(__file__).with_name("banner.svg").write_text(banner(), encoding="utf-8")
