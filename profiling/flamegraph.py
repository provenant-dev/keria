#!/usr/bin/env python3
"""Convert a py-spy speedscope JSON profile into a self-contained SVG flamegraph.

Why: on WSL the speedscope web/CLI viewers are awkward (no local browser, file://
paths don't reach the Windows browser). A plain .svg opens directly in any browser
(double-click from \\\\wsl.localhost\\... or after copying to /mnt/c/...), offline, with
no server. Identical stacks are merged (the "left-heavy" view), so the widest boxes are
the biggest total cost. Hover a box for its full name + percentage; click to zoom.

Usage:
    flamegraph.py <speedscope.json> [out.svg]
    flamegraph.py --all          # convert every *.cpu.json + *.idle.json in profiles/
"""
import html
import json
import sys
from pathlib import Path

# KERI's recursive-descent parsing produces very deep stacks; the tree walk below
# can exceed Python's default recursion limit on long profiles (e.g. rot N=500).
sys.setrecursionlimit(100000)

WIDTH = 1200
ROW = 16
PAD = 24
FONT = 12


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    frames = d["shared"]["frames"]
    prof = d["profiles"][0]
    samples = prof["samples"]
    weights = prof.get("weights") or [1] * len(samples)
    return frames, samples, weights


def build_tree(frames, samples, weights):
    """Merge all stacks into a tree; node = {name, value, children{name:node}}."""
    root = {"name": "root", "value": 0.0, "children": {}}
    for stack, w in zip(samples, weights):
        root["value"] += w
        node = root
        for idx in stack:  # root-first order
            name = frames[idx].get("name", "?")
            fr = frames[idx]
            fl = fr.get("file")
            line = fr.get("line")
            label = name if not fl else f"{name} ({Path(fl).name}:{line})"
            child = node["children"].get(label)
            if child is None:
                child = {"name": label, "value": 0.0, "children": {}}
                node["children"][label] = child
            child["value"] += w
            node = child
    return root


def color(name):
    h = 0
    for c in name:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    r = 205 + (h % 50)
    g = 80 + (h // 50 % 130)
    b = 50 + (h // 6500 % 60)
    return f"rgb({r},{g},{b})"


def layout(node, x, depth, total, rects, max_depth):
    """Assign each node a rect; children laid left-to-right under the parent."""
    w = node["value"] / total * WIDTH
    if node["name"] != "root":
        rects.append((x, depth, w, node["name"], node["value"]))
        max_depth[0] = max(max_depth[0], depth)
    cx = x
    for child in sorted(node["children"].values(), key=lambda c: -c["value"]):
        cw = child["value"] / total * WIDTH
        layout(child, cx, depth + 1, total, rects, max_depth)
        cx += cw


def render(root, title):
    total = root["value"]
    rects, max_depth = [], [0]
    layout(root, 0, 0, total, rects, max_depth)
    height = (max_depth[0] + 1) * ROW + PAD * 2 + 20
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'font-family="monospace" font-size="{FONT}">',
        f'<rect width="{WIDTH}" height="{height}" fill="#fafafa"/>',
        f'<text x="6" y="16" font-size="13" font-weight="bold">{html.escape(title)} '
        f'({total:.1f}s total, merged stacks)</text>',
    ]
    for x, depth, w, name, val in rects:
        if w < 0.3:
            continue
        y = height - PAD - (depth + 1) * ROW
        pct = val / total * 100
        esc = html.escape(name)
        out.append(
            f'<g><title>{esc} — {val:.2f}s ({pct:.1f}%)</title>'
            f'<rect x="{x:.1f}" y="{y}" width="{max(w-0.5,0.5):.1f}" height="{ROW-1}" '
            f'fill="{color(name)}" stroke="#fff" stroke-width="0.3"/>'
        )
        if w > 40:  # only label boxes wide enough to read
            label = name if len(name) * 7 < w else name[: int(w / 7)] + "…"
            out.append(
                f'<text x="{x+2:.1f}" y="{y+ROW-4}" fill="#000">{html.escape(label)}</text>'
            )
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


def convert(src, dst=None):
    frames, samples, weights = load(src)
    root = build_tree(frames, samples, weights)
    svg = render(root, Path(src).stem)
    dst = dst or str(Path(src).with_suffix(".svg"))
    Path(dst).write_text(svg)
    return dst


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--all":
        prof = Path(__file__).parent / "profiles"
        files = sorted(prof.glob("*.cpu.json")) + sorted(prof.glob("*.idle.json"))
        ok = 0
        for f in files:
            if f.stat().st_size == 0:
                print("skip (empty):", f.name)
                continue
            try:
                convert(f)
                ok += 1
            except Exception as e:  # truncated/invalid profile — skip, don't abort
                print(f"skip ({type(e).__name__}): {f.name}")
        print(f"converted {ok}/{len(files)} profiles")
        return
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print("wrote", convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))


if __name__ == "__main__":
    main()
