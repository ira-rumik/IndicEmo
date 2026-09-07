"""Resumable execution of the frozen five-way Expression Quality protocol."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from . import clients
from .io import (
    atomic_json,
    environment_record,
    load_cases,
    read_jsonl,
    sha256,
    systems_for,
    wav_info,
)
from .protocol import case_prompt, validate_and_enrich


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judge", required=True, choices=tuple(clients.JUDGE_MODELS))
    parser.add_argument("--generation-run", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--selection-file", type=Path)
    parser.add_argument("--only-id", action="append", default=[])
    parser.add_argument("--per-delivery", type=int, default=5)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate all selected inputs; do not call APIs",
    )
    parser.add_argument(
        "--judge-model", help="Use another model in a separately recorded run"
    )
    parser.add_argument("--vertex-project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--vertex-location", default="global")
    parser.add_argument(
        "--qwen-url",
        default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    return parser.parse_args()


def select_cases(args, cases):
    by_id = {c["id"]: c for c in cases}
    if args.selection_file:
        selection = json.loads(args.selection_file.read_text())
        ids = selection["ids"] if isinstance(selection, dict) else selection
        if (
            not isinstance(ids, list)
            or len(ids) != len(set(ids))
            or set(ids) - set(by_id)
        ):
            raise ValueError("Selection must contain unique, known task IDs")
        selected = [by_id[i] for i in ids]
    elif args.only_id:
        selected = cases
    else:
        counts = Counter()
        selected = []
        for case in cases:
            if counts[case["target_delivery"]] < args.per_delivery:
                selected.append(case)
                counts[case["target_delivery"]] += 1
    if args.only_id:
        wanted = set(args.only_id)
        selected = [c for c in selected if c["id"] in wanted]
        if wanted != {c["id"] for c in selected}:
            raise ValueError("Requested IDs are absent from the selection")
    if not selected:
        raise ValueError("Empty selection")
    if not args.only_id and not args.selection_file:
        counts = Counter(c["target_delivery"] for c in selected)
        if len(counts) != 5 or set(counts.values()) != {args.per_delivery}:
            raise ValueError(f"Cannot construct balanced selection: {dict(counts)}")
    return selected


def build_config(args, template, selected):
    # Per-system fingerprints are independent of the judge's A-E ordering.
    conditions = {}
    for case in selected:
        for label, path in case["audio"].items():
            if path is None:
                raise ValueError(
                    f"Missing recording for {case['id']}/{case['blind_mapping'][label]}"
                )
        conditions[case["id"]] = {
            "delivery": case["target_delivery"],
            "transcript_sha256": hashlib.sha256(
                case["transcript"].encode()
            ).hexdigest(),
            "audio_sha256": {
                case["blind_mapping"][label]: wav_info(Path(path))["audio_sha256"]
                for label, path in case["audio"].items()
            },
        }
    return {
        "runner_version": "1.1.0",
        "prompt_version": "emotion_comparative_5way_v1",
        "judge": args.judge,
        "model": clients.JUDGE_MODELS[args.judge],
        "prompt_sha256": hashlib.sha256(template.encode()).hexdigest(),
        "schema_sha256": sha256(
            clients.ROOT / "schemas/emotion_comparative_5way_v1.schema.json"
        ),
        "ids": [c["id"] for c in selected],
        "conditions": conditions,
        "systems": systems_for(args.generation_run),
        "blind_mappings": {c["id"]: c["blind_mapping"] for c in selected},
        "vertex_project": args.vertex_project,
        "vertex_location": args.vertex_location,
        "qwen_url": args.qwen_url,
        "sampling": {
            "gemini": {"temperature": 0.1, "max_output_tokens": 8192},
            "qwen": {"temperature": 0.1, "max_tokens": 5000},
            "openai": {"max_output_tokens": 5000},
        }[args.judge],
        "environment": environment_record(),
    }


def evaluate(args, template, case, run_id):
    prompt = case_prompt(template, case)
    common = {
        key: case[key] for key in ("id", "type", "target_delivery", "blind_mapping")
    }
    common.update(
        judge=args.judge,
        judge_model=clients.JUDGE_MODELS[args.judge],
        run_id=run_id,
        prompt_version="emotion_comparative_5way_v1",
    )
    errors, rejected = [], []
    fatal = False
    for attempt in range(1, args.attempts + 1):
        raw = None
        usage = {}
        try:
            raw, usage = getattr(clients, f"judge_{args.judge}")(args, prompt, case)
            result = validate_and_enrich(copy.deepcopy(raw), case)
            return {
                **common,
                "attempt": attempt,
                "retry_errors": errors,
                "rejected_responses": rejected,
                "usage": usage,
                "raw_response": raw,
                "result": result,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as error:
            if isinstance(error, clients.JudgeDecodeError):
                raw = error.response_text
            message = str(error)
            for key in ("QWEN_API_KEY", "OPENAI_API_KEY"):
                secret = os.getenv(key)
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            errors.append(f"attempt {attempt}: {type(error).__name__}: {message}")
            if raw is not None:
                rejected.append({"attempt": attempt, "response": raw, "usage": usage})
            status = clients.http_status(error)
            if status in (400, 401, 403, 404):
                fatal = status in (401, 403, 404)
                break
            if attempt < args.attempts:
                time.sleep(min(30, attempt * 2))
    return {
        **common,
        "error": "; ".join(errors),
        "fatal": fatal,
        "rejected_responses": rejected,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def completed_ids(output, selected, run_id):
    by_id = {c["id"]: c for c in selected}
    complete = set()
    for row in read_jsonl(output) if output.exists() else []:
        if row.get("run_id") != run_id or row.get("id") not in by_id:
            raise ValueError(
                "Existing results do not belong to this run; use a new output directory"
            )
        if "error" not in row:
            if row["id"] in complete:
                raise ValueError(f"Duplicate successful result: {row['id']}")
            raw = row.get("raw_response")
            if not isinstance(raw, dict):
                raise ValueError("Successful result has no raw response")
            validated = validate_and_enrich(copy.deepcopy(raw), by_id[row["id"]])
            if row.get("result") != validated:
                raise ValueError(
                    f"Stored result differs from raw response: {row['id']}"
                )
            complete.add(row["id"])
    return complete


def main():
    args = arguments()
    if args.workers < 1 or args.attempts < 1 or not 1 <= args.per_delivery <= 20:
        raise ValueError("Use positive workers/attempts and 1–20 prompts per delivery")
    if args.judge_model:
        clients.JUDGE_MODELS[args.judge] = args.judge_model
    selected = select_cases(args, load_cases(args))
    template = (clients.ROOT / "prompts/emotion_comparative_5way_v1.txt").read_text()
    config = build_config(args, template, selected)
    counts = dict(Counter(c["target_delivery"] for c in selected))
    print(
        f"{args.judge}: {len(selected)} tasks, {len(selected) * 5} validated WAVs; delivery counts={counts}",
        flush=True,
    )
    print(
        f"Request budget: up to {len(selected) * args.attempts} attempts before resume filtering (each includes 5 clips)",
        flush=True,
    )
    if args.dry_run:
        print("PASS: dry run; no API calls or result files")
        return
    config_path = args.output_dir / f"{args.judge}_config.json"
    output = args.output_dir / "results" / f"{args.judge}.jsonl"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.output_dir / f".{args.judge}.lock"), timeout=0):
        if config_path.exists():
            if json.loads(config_path.read_text()) != config:
                raise ValueError(
                    "Run configuration changed; use a fresh output directory"
                )
        elif output.exists():
            raise ValueError(
                "Existing results lack a configuration; use a fresh output directory"
            )
        run_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        complete = completed_ids(output, selected, run_id)
        pending = [c for c in selected if c["id"] not in complete]
        if pending:
            if args.judge == "gemini":
                if not args.vertex_project:
                    raise ValueError("Set GOOGLE_CLOUD_PROJECT or --vertex-project")
                import google.auth

                google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            elif not os.getenv(
                {"qwen": "QWEN_API_KEY", "openai": "OPENAI_API_KEY"}[args.judge]
            ):
                raise ValueError(f"Missing API key for {args.judge}")
        atomic_json(config_path, config)
        print(f"{len(complete)} complete; {len(pending)} pending", flush=True)
        failed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(evaluate, args, template, c, run_id) for c in pending
            ]
            for future in concurrent.futures.as_completed(futures):
                if future.cancelled():
                    failed += 1
                    continue
                row = future.result()
                if row.get("fatal"):
                    for queued in futures:
                        queued.cancel()
                clients.append_jsonl(output, row)
                failed += int("error" in row)
                print(f"{row['id']}: {'error' if 'error' in row else 'ok'}", flush=True)
        if failed:
            raise SystemExit(
                f"{failed} tasks failed; inspect {output} and resume the same command"
            )


if __name__ == "__main__":
    main()
