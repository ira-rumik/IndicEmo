#!/usr/bin/env python3
"""Reproduce the published IndicEmo Benchmark score table."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq


DELIVERIES = ("Happy", "Sad", "Angry", "Excited", "Professional")
EMOTIONS = ("Happy", "Sad", "Angry", "Excited")
SYSTEMS = (
    ("gemini", "Gemini 3.1 Flash TTS Preview"),
    ("rumik_oss_1", "Rumik-OSS 1"),
    ("cartesia_sonic_preview", "Cartesia Sonic Preview"),
    ("cartesia_sonic_3_5", "Cartesia Sonic 3.5"),
    ("elevenlabs", "ElevenLabs Eleven v3"),
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--judgments",
        type=Path,
        default=Path(__file__).resolve().parent / "benchmark/judgments.jsonl",
        help="Flattened clip judgments from this release.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent
        / "benchmark/results/consensus_scores.csv",
        help="Destination CSV.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that an existing output exactly matches recomputation.",
    )
    return parser.parse_args()


def compute(judgments: Path) -> list[dict]:
    if judgments.suffix == ".jsonl":
        rows = [
            json.loads(line)
            for line in judgments.read_text().splitlines()
            if line.strip()
        ]
    else:
        rows = pq.read_table(judgments).to_pylist()
    strict = [row for row in rows if row["strict_common_set"]]
    prompt_ids = {row["prompt_id"] for row in strict}
    if len(prompt_ids) != 98:
        raise ValueError(f"Expected 98 strict-common prompts, found {len(prompt_ids)}")

    scores: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    judge_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in strict:
        if (
            row["system_id"] not in {s for s, _ in SYSTEMS}
            or row["target_delivery"] not in DELIVERIES
        ):
            raise ValueError("Unexpected system or delivery in published judgments")
        score = row["expression_quality_score"]
        if type(score) is not int or not 1 <= score <= 5:
            raise ValueError(f"Invalid integer score: {score!r}")
        key = (row["system_id"], row["target_delivery"], row["prompt_id"])
        scores[key].append(score)
        judge_ids[(row["system_id"], row["prompt_id"])].add(row["judge_id"])

    for key, values in scores.items():
        if len(values) != 3:
            raise ValueError(
                f"Expected three judge scores for {key}, found {len(values)}"
            )
    for key, judges in judge_ids.items():
        if judges != {"gemini", "qwen", "openai"}:
            raise ValueError(f"Unexpected judge set for {key}: {sorted(judges)}")
    for system, _ in SYSTEMS:
        if {p for s, p in judge_ids if s == system} != prompt_ids:
            raise ValueError(f"Incomplete strict-common coverage: {system}")
    if len(strict) != 98 * 5 * 3 or len(scores) != 98 * 5:
        raise ValueError("Unexpected row count or conflicting delivery labels")

    prompt_consensus: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (system_id, delivery, _prompt_id), values in scores.items():
        prompt_consensus[(system_id, delivery)].append(float(statistics.median(values)))

    result = []
    for system_id, system_name in SYSTEMS:
        category_scores = {}
        category_counts = {}
        for delivery in DELIVERIES:
            values = prompt_consensus[(system_id, delivery)]
            if not values:
                raise ValueError(f"No values for {system_id}/{delivery}")
            category_scores[delivery] = sum(values) / len(values)
            category_counts[delivery] = len(values)

        result.append(
            {
                "system_id": system_id,
                "system_name": system_name,
                "overall_consensus_score": sum(category_scores[d] for d in DELIVERIES)
                / len(DELIVERIES),
                "emotion_only_consensus_score": sum(
                    category_scores[d] for d in EMOTIONS
                )
                / len(EMOTIONS),
                "happy": category_scores["Happy"],
                "sad": category_scores["Sad"],
                "angry": category_scores["Angry"],
                "excited": category_scores["Excited"],
                "professional": category_scores["Professional"],
                "happy_prompts": category_counts["Happy"],
                "sad_prompts": category_counts["Sad"],
                "angry_prompts": category_counts["Angry"],
                "excited_prompts": category_counts["Excited"],
                "professional_prompts": category_counts["Professional"],
                "strict_common_prompts": len(prompt_ids),
                "judge_count": 3,
                "aggregation": "per_prompt_judge_median_then_delivery_macro_average",
            }
        )

    return sorted(result, key=lambda row: row["overall_consensus_score"], reverse=True)


def serialized(rows: list[dict]) -> str:
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> None:
    args = arguments()
    rows = compute(args.judgments)
    content = serialized(rows)
    if args.check:
        if not args.output.exists():
            raise FileNotFoundError(args.output)
        if args.output.read_text(encoding="utf-8") != content:
            raise RuntimeError(
                f"Published score table does not match raw judgments: {args.output}"
            )
        print(f"PASS: {args.output} exactly matches {args.judgments}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"Wrote {len(rows)} systems to {args.output}")


if __name__ == "__main__":
    main()
