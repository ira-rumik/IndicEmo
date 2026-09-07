#!/usr/bin/env python3
"""Aggregate a fresh three-judge run on its common valid prompt intersection."""

import argparse
import copy
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from aggregate import DELIVERIES, EMOTIONS, SYSTEMS
from evaluation.io import read_jsonl, atomic_json
from evaluation.protocol import validate_and_enrich


def summarize(directory: Path, *, details=False):
    by_judge = {}
    universe = set()
    configs = {}
    identities = list(SYSTEMS)
    config_paths = {
        judge: directory.parent / f"{judge}_config.json"
        for judge in ("gemini", "qwen", "openai")
    }
    if any(p.exists() for p in config_paths.values()):
        if not all(p.exists() for p in config_paths.values()):
            raise ValueError(
                "Incomplete run configurations: all three judges must have a configuration"
            )
        configs = {j: json.loads(p.read_text()) for j, p in config_paths.items()}
        reference = configs["gemini"]
        for j, config in configs.items():
            for key in (
                "ids",
                "conditions",
                "systems",
                "prompt_sha256",
                "schema_sha256",
                "runner_version",
            ):
                if config[key] != reference[key]:
                    raise ValueError(
                        f"Judges evaluated different inputs/protocols: {j}/{key}"
                    )
        identities = [(s["id"], s["name"]) for s in reference["systems"]]
        universe = set(reference["ids"])
    for judge in ("gemini", "qwen", "openai"):
        cases = {}
        models = set()
        for row in read_jsonl(directory / f"{judge}.jsonl"):
            if row.get("judge", judge) != judge:
                raise ValueError(f"Judge identity mismatch in {judge}.jsonl")
            if row.get("judge_model"):
                models.add(row["judge_model"])
            if configs:
                import hashlib

                expected_run = hashlib.sha256(
                    json.dumps(configs[judge], sort_keys=True).encode()
                ).hexdigest()
                if row.get("run_id") != expected_run or row["id"] not in universe:
                    raise ValueError(
                        f"Result provenance mismatch in {judge}/{row['id']}"
                    )
                config = configs[judge]
                if "model" in config and row.get("judge_model") != config["model"]:
                    raise ValueError(f"Judge model mismatch: {judge}/{row['id']}")
                if "blind_mappings" in config:
                    if row.get("blind_mapping") != config["blind_mappings"][row["id"]]:
                        raise ValueError(f"Blind mapping mismatch: {judge}/{row['id']}")
                    condition = config["conditions"][row["id"]]
                    if row.get("target_delivery") != condition["delivery"]:
                        raise ValueError(f"Delivery mismatch: {judge}/{row['id']}")
                    if "error" not in row:
                        validated = validate_and_enrich(
                            copy.deepcopy(row.get("raw_response")), row
                        )
                        if row.get("result") != validated:
                            raise ValueError(
                                f"Stored result differs from raw response: {judge}/{row['id']}"
                            )
            else:
                universe.add(row["id"])
            if "error" not in row:
                if row["id"] in cases:
                    raise ValueError(
                        f"Duplicate successful result in {judge}/{row['id']}"
                    )
                cases[row["id"]] = row
        if len(models) > 1:
            raise ValueError(f"Multiple model versions in {judge}.jsonl")
        by_judge[judge] = cases
    common = set.intersection(*(set(rows) for rows in by_judge.values()))
    if not common:
        raise ValueError("No common valid prompts across all three judges")
    expected_systems = {s for s, _ in identities}
    grouped = defaultdict(list)
    counts = defaultdict(int)
    for sample_id in sorted(common):
        deliveries, scores = set(), defaultdict(list)
        for judge, cases in by_judge.items():
            row = cases[sample_id]
            deliveries.add(row["target_delivery"])
            if (
                set(row["blind_mapping"]) != set("ABCDE")
                or set(row["blind_mapping"].values()) != expected_systems
            ):
                raise ValueError(f"Invalid system mapping: {judge}/{sample_id}")
            seen = set()
            if row["result"].get("invalid_clips"):
                raise ValueError(f"Invalid clips in {judge}/{sample_id}")
            for clip in row["result"]["clips"]:
                system = row["blind_mapping"][clip["clip_id"]]
                value = clip["expression_quality_score"]
                if system in seen or type(value) is not int or not 1 <= value <= 5:
                    raise ValueError(f"Invalid score/system in {judge}/{sample_id}")
                seen.add(system)
                scores[system].append(value)
            if seen != expected_systems:
                raise ValueError(f"System coverage mismatch in {judge}/{sample_id}")
        if len(deliveries) != 1 or not deliveries <= set(DELIVERIES):
            raise ValueError(f"Inconsistent delivery: {sample_id}")
        delivery = deliveries.pop()
        counts[delivery] += 1
        for system, values in scores.items():
            grouped[system, delivery].append(statistics.median(values))
    if set(counts) != set(DELIVERIES):
        raise ValueError(
            "All five deliveries must have common valid cases for the overall table"
        )
    output = []
    for system, name in identities:
        means = {d: statistics.mean(grouped[system, d]) for d in DELIVERIES}
        output.append(
            {
                "system_id": system,
                "system_name": name,
                "overall_consensus_score": statistics.mean(means.values()),
                "emotion_only_consensus_score": statistics.mean(
                    means[d] for d in EMOTIONS
                ),
                **{d.lower(): means[d] for d in DELIVERIES},
                **{d.lower() + "_prompts": counts[d] for d in DELIVERIES},
                "strict_common_prompts": len(common),
            }
        )
    exclusions = {j: sorted(universe - set(rows)) for j, rows in by_judge.items()}
    result = sorted(output, key=lambda r: r["overall_consensus_score"], reverse=True)
    if details:
        return result, {
            "expected_or_observed_tasks": len(universe),
            "common_valid_tasks": len(common),
            "common_task_ids": sorted(common),
            "delivery_counts": dict(counts),
            "missing_valid_by_judge": exclusions,
            "excluded_task_ids": sorted(universe - common),
            "all_judges_missing": sorted(
                universe - set.union(*(set(r) for r in by_judge.values()))
            ),
        }
    return result, exclusions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("runs/consensus_scores.csv")
    )
    args = parser.parse_args()
    rows, coverage = summarize(args.results_dir, details=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"Common valid prompts: {rows[0]['strict_common_prompts']}; missing valid cases: {coverage['missing_valid_by_judge']}"
    )
    for row in rows:
        print(
            f"{row['system_name']}: {row['overall_consensus_score']:.3f}/5; emotions only {row['emotion_only_consensus_score']:.3f}/5"
        )
    print(f"Saved {args.output}")
    atomic_json(args.output.with_suffix(".coverage.json"), coverage)


if __name__ == "__main__":
    main()
