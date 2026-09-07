"""Render IndicEmo's recorded consensus scores without changing the metric.

Run from ayatts/: python build_figures.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.offsetbox import AnnotationBbox, DrawingArea, OffsetImage
from matplotlib.patches import Rectangle
from matplotlib.text import Text

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from chart_style import PALETTE, gradient_bars, save_chart  # noqa: E402

SOURCE = HERE / "benchmark/results/consensus_scores.csv"
CATEGORIES = ("happy", "sad", "angry", "excited", "professional")
NAMES = {
    "gemini": ("Gemini", "3.1 Flash TTS Preview"),
    "rumik_oss_1": ("rumik-oss 1", "Ira · one voice across all conditions"),
    "cartesia_sonic_preview": ("Cartesia", "Sonic 3.6"),
    "cartesia_sonic_3_5": ("Cartesia", "Sonic 3.5"),
    "elevenlabs": ("ElevenLabs", "Eleven v3"),
}
LOGOS = {
    "gemini": "google.png",
    "rumik_oss_1": "rumiklogoonly.png",
    "cartesia_sonic_preview": "cartesia.png",
    "cartesia_sonic_3_5": "cartesia.png",
    "elevenlabs": "elevenlabs.png",
}
COLORS = {
    "gemini": PALETTE["gemini"],
    "rumik_oss_1": PALETTE["rumik"],
    "cartesia_sonic_preview": PALETTE["cartesia_preview"],
    "cartesia_sonic_3_5": PALETTE["cartesia_35"],
    "elevenlabs": PALETTE["elevenlabs"],
}


def logo_box(system, size=23):
    path = HERE / "assets" / "logos" / LOGOS[system]
    if system == "rumik_oss_1" and not path.exists():
        box = DrawingArea(size, size, 0, 0)
        box.add_artist(
            Rectangle(
                (0, 0),
                size,
                size,
                facecolor="white",
                edgecolor="#999999",
                linewidth=0.8,
                linestyle="--",
            )
        )
        box.add_artist(
            Text(
                size / 2,
                size / 2,
                "LOGO",
                ha="center",
                va="center",
                fontsize=5,
                color="#888888",
            )
        )
        return box
    pixels = plt.imread(path)
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, None], 3, axis=2)
    return OffsetImage(pixels, zoom=size / max(pixels.shape[:2]))


def add_logo(ax, system, position, coords, size=23):
    artist = AnnotationBbox(
        logo_box(system, size),
        position,
        xycoords=coords,
        frameon=False,
        annotation_clip=False,
        pad=0,
    )
    ax.add_artist(artist)


def load_scores(path):
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != 5 or {r["system_id"] for r in rows} != set(NAMES):
        raise ValueError("Expected the five systems in the recorded IndicEmo release")
    for row in rows:
        for key in (
            *CATEGORIES,
            "overall_consensus_score",
            "emotion_only_consensus_score",
        ):
            row[key] = float(row[key])
            if not math.isfinite(row[key]) or not 1 <= row[key] <= 5:
                raise ValueError(f"Invalid {key}: {row[key]}")
        if int(row["strict_common_prompts"]) != 98 or int(row["judge_count"]) != 3:
            raise ValueError(
                "This figure documents the recorded 98-task, three-judge result"
            )
        counts = [int(row[f"{key}_prompts"]) for key in CATEGORIES]
        if counts != [20, 20, 19, 20, 19]:
            raise ValueError("Unexpected common-set category counts")
        if not math.isclose(
            row["overall_consensus_score"], np.mean([row[k] for k in CATEGORIES])
        ):
            raise ValueError("Overall is not the five-category macro-average")
        if not math.isclose(
            row["emotion_only_consensus_score"],
            np.mean([row[k] for k in CATEGORIES[:4]]),
        ):
            raise ValueError("Emotion-only is not the four-category macro-average")
    return sorted(rows, key=lambda r: r["overall_consensus_score"], reverse=True)


def style_axis(ax):
    ax.set_ylim(0, 5.25)
    ax.set_yticks(range(6))
    ax.set_ylabel("Expression Quality ↑", fontsize=12, labelpad=12)
    ax.grid(axis="y", color="#E7E7E7", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=9, labelsize=11)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#C6C6C6")


def save(fig, output, stem):
    for suffix in ("png", "svg"):
        path = output / f"{stem}.{suffix}"
        save_chart(fig, path)
        print(f"Saved {path}")
    plt.close(fig)


def draw(rows, output):
    plt.rcParams.update(
        {
            "font.family": ["Inter", "DejaVu Sans"],
            "font.size": 11,
            "text.color": "#222222",
            "axes.labelcolor": "#333333",
            "xtick.color": "#333333",
            "ytick.color": "#666666",
            "svg.fonttype": "none",
            "axes.unicode_minus": False,
        }
    )
    names = {
        "gemini": "Gemini 3.1\nFlash TTS Preview",
        "rumik_oss_1": "rumik-oss 1",
        "cartesia_sonic_preview": "Cartesia\nSonic 3.6",
        "cartesia_sonic_3_5": "Cartesia\nSonic 3.5",
        "elevenlabs": "ElevenLabs\nEleven v3",
    }
    output.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 5.8))
    fig.subplots_adjust(left=0.075, right=0.98, bottom=0.255, top=0.84)
    fig.text(0.075, 0.925, "IndicEmo", fontsize=21, weight="semibold")
    style_axis(ax)
    ax.set_ylabel("Overall Expression Quality ↑", fontsize=12, labelpad=12)
    x = np.arange(len(rows))
    bars = ax.bar(
        x,
        [r["overall_consensus_score"] for r in rows],
        width=0.55,
        color=[COLORS[r["system_id"]] for r in rows],
    )
    ax.bar_label(bars, fmt="%.2f", padding=6, fontsize=12, color="#222222")
    ax.set_xticks(x, [names[r["system_id"]] for r in rows])
    ax.tick_params(axis="x", pad=43)
    for i, row in enumerate(rows):
        add_logo(ax, row["system_id"], (i, -0.082), ax.get_xaxis_transform())
    ax.margins(x=0.055)
    gradient_bars(ax, bars)
    fig.text(
        0.075,
        0.04,
        "98 prompts · 3 audio judges · Macro-average of per-prompt median scores (1–5)",
        fontsize=9,
        color="#666666",
    )
    save(fig, output, "indicemo_results")

    fig, ax = plt.subplots(figsize=(13, 5.8))
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.16, top=0.72)
    fig.text(
        0.07, 0.925, "IndicEmo — Per-delivery results", fontsize=20, weight="semibold"
    )
    style_axis(ax)
    x = np.arange(5)
    all_bars = []
    for i, row in enumerate(rows):
        system = row["system_id"]
        color = COLORS[system]
        bars = ax.bar(
            x + (i - 2) * 0.165, [row[k] for k in CATEGORIES], width=0.145, color=color
        )
        ax.bar_label(bars, fmt="%.2f", padding=4, fontsize=8.5)
        all_bars.extend(bars)
        # Compact, logo-bearing legend; identities stay in bar order.
        left = 0.074 + i * 0.182
        add_logo(ax, system, (left + 0.012, 0.823), fig.transFigure, size=21)
        fig.add_artist(
            Rectangle(
                (left + 0.035, 0.806),
                0.009,
                0.02,
                transform=fig.transFigure,
                facecolor=color,
                edgecolor="none",
            )
        )
        fig.text(left + 0.051, 0.822, names[system], va="center", fontsize=9.5)
    ax.set_xticks(x, ["Happy", "Sad", "Angry", "Excited", "Professional"])
    ax.margins(x=0.025)
    gradient_bars(ax, all_bars)
    fig.text(
        0.07,
        0.04,
        "Common-set prompts: 20 / 20 / 19 / 20 / 19 · Professional is a speaking style.",
        fontsize=9,
        color="#666666",
    )
    save(fig, output, "indicemo_by_delivery")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, default=SOURCE)
    parser.add_argument("--output-dir", type=Path, default=HERE / "assets")
    args = parser.parse_args()
    rows = load_scores(args.scores)
    print(f"Source: {args.scores}")
    print(f"SHA-256: {hashlib.sha256(args.scores.read_bytes()).hexdigest()}")
    draw(rows, args.output_dir)


if __name__ == "__main__":
    main()
