"""Shared, score-independent gradients for the benchmark figures."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
from matplotlib import rc_context
from matplotlib.colors import to_hex, to_rgb

PALETTE = {
    "gemini": "#4774C8",
    "rumik": "#18998D",
    "cartesia_preview": "#8879BA",
    "cartesia_35": "#E2C64D",
    "elevenlabs": "#C67F94",
    "grok": "#586779",
    "inworld": "#738ACC",
    "orpheus": "#927ABD",
}


def gradient_bars(ax, bars):
    """Fill existing bar rectangles without changing geometry or axis limits."""
    fig = ax.figure
    specs = getattr(fig, "_benchmark_gradients", [])
    limits = ax.get_xlim(), ax.get_ylim()
    for bar in bars:
        base = np.array(to_rgb(bar.get_facecolor()))
        bottom = base * 0.88
        top = base * 0.78 + 0.22
        # Every model uses the same relative shading; color does not encode score.
        values = np.linspace(0, 1, 256)[:, None, None]
        pixels = bottom[None, None, :] * (1 - values) + top[None, None, :] * values
        index = len(specs)
        bar_id, overlay_id = f"benchmark-bar-{index}", f"benchmark-shading-{index}"
        bar.set_gid(bar_id)
        overlay = ax.imshow(
            pixels,
            origin="lower",
            aspect="auto",
            interpolation="bicubic",
            extent=(
                bar.get_x(),
                bar.get_x() + bar.get_width(),
                bar.get_y(),
                bar.get_y() + bar.get_height(),
            ),
            zorder=bar.get_zorder() + 0.01,
        )
        overlay.set_clip_path(bar)
        overlay.set_gid(overlay_id)
        specs.append((bar_id, overlay_id, to_hex(bottom), to_hex(top)))
    ax.set_xlim(limits[0])
    ax.set_ylim(limits[1])
    fig._benchmark_gradients = specs


def save_chart(fig, path):
    """Use raster shading for PNG, editable native linear gradients for SVG."""
    with rc_context({"image.composite_image": False}):
        fig.savefig(path, dpi=200, facecolor="white")
    if path.suffix != ".svg":
        return
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    tree = ET.parse(path)
    root = tree.getroot()
    defs = root.find(f"{{{ns}}}defs")
    if defs is None:
        defs = ET.SubElement(root, f"{{{ns}}}defs")
    ids = {node.get("id"): node for node in root.iter() if node.get("id")}
    parents = {child: parent for parent in root.iter() for child in parent}
    for bar_id, overlay_id, bottom, top in getattr(fig, "_benchmark_gradients", []):
        gradient_id = bar_id + "-gradient"
        gradient = ET.SubElement(
            defs,
            f"{{{ns}}}linearGradient",
            {
                "id": gradient_id,
                "x1": "0%",
                "y1": "100%",
                "x2": "0%",
                "y2": "0%",
            },
        )
        ET.SubElement(gradient, f"{{{ns}}}stop", {"offset": "0%", "stop-color": bottom})
        ET.SubElement(gradient, f"{{{ns}}}stop", {"offset": "100%", "stop-color": top})
        shape = ids[bar_id].find(f"{{{ns}}}path")
        if shape is None or overlay_id not in ids:
            raise ValueError(f"Missing SVG gradient geometry: {bar_id}")
        shape.set("style", f"fill: url(#{gradient_id})")
        parents[ids[overlay_id]].remove(ids[overlay_id])
    tree.write(path, encoding="utf-8", xml_declaration=True)
