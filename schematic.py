"""Layout diagrams for the planner (matplotlib). Radiators are drawn at their distance from the heat source."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

SUPPLY, RETURN, INK, MUTED, BRASS = "#c8322b", "#2459b8", "#18212d", "#5a6778", "#9a6418"


def _mix(a, b, t):
    ca, cb = to_rgb(a), to_rgb(b)
    return tuple(ca[i] * (1 - t) + cb[i] * t for i in range(3))


def _pipe(ax, xs, ys, c, lw=3.0):
    ax.plot(xs, ys, color=c, lw=lw, solid_capstyle="round", solid_joinstyle="round", zorder=2)


def _radiator(ax, x, y, label, label_dy=0.42):
    ax.add_patch(FancyBboxPatch((x - 0.32, y - 0.2), 0.64, 0.4, boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc="#eef1f6", ec=INK, lw=1.2, zorder=5))
    for i in range(4):
        xx = x - 0.2 + i * 0.13
        ax.plot([xx, xx], [y - 0.13, y + 0.13], color=MUTED, lw=0.8, zorder=6)
    ax.text(x, y + label_dy, label, ha="center", va="bottom", fontsize=8.5, fontweight="bold", zorder=7)


def _pump(ax, x, y):
    ax.add_patch(Circle((x, y), 0.2, fc="white", ec=INK, lw=1.3, zorder=7))
    ax.add_patch(Polygon([[x - 0.08, y + 0.11], [x - 0.08, y - 0.11], [x + 0.12, y]], closed=True, fc=INK, zorder=8))


def _boiler(ax, y0, y1, text):
    ax.add_patch(Rectangle((-1.75, y0), 1.25, y1 - y0, fc="#eef1f6", ec=INK, lw=1.3, zorder=5))
    ax.text(-1.125, (y0 + y1) / 2, text, ha="center", va="center", fontsize=8.5, fontweight="bold", zorder=6)


def draw(option, result, source: str = "condensing"):
    """Return a matplotlib Figure with the layout of one option."""
    rads, fam = result.rads, option.family
    n, dmax = len(rads), max(r.d for r in rads)
    span = 12.0
    x0 = 2.6 if fam == "ps" else (2.2 if fam == "manifold" else 1.0)
    X = lambda d: x0 + d / dmax * span
    label_src = "Heat pump" if source == "heatpump" else "Boiler"
    ys, yr = 3.0, 0.0
    if fam == "manifold":
        h = 0.8 * n + 1.2
        fig, ax = plt.subplots(figsize=(10, max(2.6, 0.45 * n + 1.4)))
        top = h - 0.6
        mx = 1.2
        ax.add_patch(Rectangle((mx - 0.12, top - 0.8 * n + 0.1), 0.24, 0.8 * n, fc="#eef1f6", ec=INK, lw=1.2, zorder=4))
        ax.text(mx, top + 0.25, "manifold", ha="center", fontsize=8, color=MUTED)
        _pipe(ax, [-0.5, mx - 0.12], [top - 0.2, top - 0.2], SUPPLY); _pump(ax, 0.3, top - 0.2)
        _pipe(ax, [mx - 0.12, -0.5], [top - 0.8 * n + 0.3, top - 0.8 * n + 0.3], RETURN)
        for i, r in enumerate(rads):
            y, x = top - 0.4 - 0.8 * i, X(r.d)
            _pipe(ax, [mx + 0.12, x - 0.32], [y + 0.1, y + 0.1], SUPPLY, 1.8)
            _pipe(ax, [x - 0.32, mx + 0.12], [y - 0.1, y - 0.1], RETURN, 1.8)
            _radiator(ax, x, y, "", 0)
            ax.text(x + 0.45, y, f"{r.id}  {r.room}", va="center", fontsize=8.5)
        _boiler(ax, top - 0.8 * n + 0.1, top, label_src)
        ax.set_xlim(-2, span + x0 + 2.6); ax.set_ylim(top - 0.8 * n - 0.4, top + 0.6)
    else:
        fig, ax = plt.subplots(figsize=(10, 3.2))
        prev_x, alt = -99, 0
        if fam in ("direct", "ps"):
            start = -0.5
            if fam == "ps":
                _pipe(ax, [-0.5, 1.4], [ys, ys], SUPPLY); _pipe(ax, [1.4, -0.5], [yr, yr], RETURN)
                _pipe(ax, [1.4, 1.4], [ys, yr], BRASS, 5); _pump(ax, 0.45, ys)
                ax.text(1.4, yr - 0.55, "header", ha="center", fontsize=8, color=MUTED)
                start = 1.4
            _pipe(ax, [start, X(dmax)], [ys, ys], SUPPLY); _pipe(ax, [X(dmax), start], [yr, yr], RETURN)
            _pump(ax, 2.0 if fam == "ps" else 0.3, ys)
            for r in rads:
                x = X(r.d)
                alt = 1 - alt if x - prev_x < 1.1 else 0
                prev_x = x
                _pipe(ax, [x, x], [ys, 1.7], SUPPLY, 1.8); _pipe(ax, [x, x], [1.3, yr], RETURN, 1.8)
                _radiator(ax, x, 1.5, r.id)
                ax.text(x, yr - 0.45 - alt * 0.35, r.room, ha="center", fontsize=7.5, color=MUTED)
            _boiler(ax, yr - 0.3, ys + 0.3, label_src)
            ax.set_ylim(-1.3, 3.6)
        elif fam == "reverse":
            yb = -1.2
            _pipe(ax, [-0.5, X(dmax)], [ys, ys], SUPPLY); _pump(ax, 0.3, ys)
            _pipe(ax, [X(rads[0].d) + 0.32, X(dmax) + 0.6, X(dmax) + 0.6, -1.125, -1.125], [yr, yr, yb, yb, yr - 0.3], RETURN)
            for r in rads:
                x = X(r.d)
                alt = 1 - alt if x - prev_x < 1.1 else 0
                prev_x = x
                _pipe(ax, [x, x], [ys, 1.7], SUPPLY, 1.8)
                _pipe(ax, [x, x, x + 0.32, x + 0.32], [1.3, 0.9, 0.9, yr], RETURN, 1.8)
                _radiator(ax, x, 1.5, r.id)
                ax.text(x, yb - 0.45 - alt * 0.35, r.room, ha="center", fontsize=7.5, color=MUTED)
            _boiler(ax, yr - 0.3, ys + 0.3, label_src)
            ax.set_ylim(-2.4, 3.6)
        elif fam == "series":
            yb = -0.6
            _pump(ax, 0.3, ys)
            prev = -0.5
            for i, r in enumerate(rads):
                x, col = X(r.d), _mix(SUPPLY, RETURN, 0.7 * i / max(1, n - 1))
                _pipe(ax, [prev, x - 0.5, x - 0.5, x - 0.32], [ys, ys, 1.5, 1.5], col, 2.4)
                _pipe(ax, [x + 0.32, x + 0.5, x + 0.5], [1.5, 1.5, ys], col, 2.4)
                _radiator(ax, x, 1.5, r.id)
                alt = 1 - alt if x - prev_x < 1.1 else 0
                prev_x = x
                ax.text(x, 0.85 - alt * 0.35, r.room, ha="center", fontsize=7.5, color=MUTED)
                prev = x + 0.5
            _pipe(ax, [prev, prev + 0.4, prev + 0.4, -1.125, -1.125], [ys, ys, yb, yb, yr - 0.3], RETURN)
            _boiler(ax, yr - 0.3, ys + 0.3, label_src)
            ax.set_ylim(-1.3, 3.6)
        ax.set_xlim(-2, span + x0 + 1.4)
    ax.axis("off")
    ax.set_title(option.name, loc="left", fontsize=11, fontweight="bold")
    ax.text(0, 0, f"Radiators drawn at their distance from the {label_src.lower()} (0–{dmax:.1f} m)",
            transform=ax.transAxes, fontsize=7.5, color=MUTED, va="top")
    fig.tight_layout()
    return fig
