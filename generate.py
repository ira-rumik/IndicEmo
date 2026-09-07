#!/usr/bin/env python3
"""Replay the archived per-task generation requests with user-supplied credentials."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import time
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from filelock import FileLock

from evaluation.clients import append_jsonl, http_status
from evaluation.io import (
    atomic_json,
    read_jsonl,
    sha256,
    unique_rows,
    wav_info,
    SYSTEM_NAMES,
)
from prepare import (
    BENCHMARK_DIR,
    REVISION,
    SYSTEMS,
    load_prompts,
    verify_release,
    write_jsonl,
)


def request_spec(system, row, endpoint=None):
    if system == "gemini":
        return {
            "model": row["model"],
            "contents": row["director_prompt"],
            "config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": row["voice"]}
                    }
                },
            },
        }
    if system == "rumik_oss_1":
        return {"endpoint": endpoint, "json": row["request"]}
    if system == "elevenlabs":
        return {
            "endpoint": f"https://api.elevenlabs.io/v1/text-to-speech/{row['voice_id']}",
            "params": {"output_format": "pcm_24000"},
            "json": row["request"],
        }
    return {
        "endpoint": "https://api.cartesia.ai/tts/bytes",
        "api_version": "2026-08-14",
        "json": row["request"],
    }


def pcm_wav(pcm):
    if not pcm or len(pcm) % 2:
        raise ValueError("Expected nonempty PCM16 response")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return buffer.getvalue()


def synthesize(args, request):
    if args.system == "gemini":
        from google import genai
        from google.genai import types

        with genai.Client(
            vertexai=True, project=args.vertex_project, location=args.vertex_location
        ) as client:
            response = client.models.generate_content(
                model=request["model"],
                contents=request["contents"],
                config=types.GenerateContentConfig(**request["config"]),
            )
        parts = response.candidates[0].content.parts
        chunks = [
            p.inline_data.data for p in parts if p.inline_data and p.inline_data.data
        ]
        if len(chunks) != 1:
            raise ValueError("Expected one generated PCM audio part")
        return pcm_wav(chunks[0])
    headers = {"Content-Type": "application/json"}
    if args.system == "elevenlabs":
        headers["xi-api-key"] = os.environ["ELEVENLABS_API_KEY"]
    elif args.system.startswith("cartesia"):
        headers.update(
            {
                "Authorization": f"Bearer {os.environ['CARTESIA_API_KEY']}",
                "Cartesia-Version": request["api_version"],
            }
        )
    response = httpx.post(
        request["endpoint"],
        json=request["json"],
        params=request.get("params"),
        headers=headers,
        timeout=300,
    )
    response.raise_for_status()
    if args.system == "elevenlabs":
        if "json" in response.headers.get("content-type", ""):
            raise ValueError("Provider returned JSON instead of PCM audio")
        return pcm_wav(response.content)
    return response.content


def generate_one(args, row, request, run_id):
    errors = []
    fatal = False
    for attempt in range(1, args.attempts + 1):
        try:
            audio = synthesize(args, request)
            output = args.output_dir / "audio" / args.system / f"{row['id']}.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            temp = output.with_suffix(".tmp")
            temp.write_bytes(audio)
            metadata = wav_info(temp)
            temp.replace(output)
            return {
                "id": row["id"],
                "status": "ok",
                "run_id": run_id,
                "audio_path": str(output.relative_to(args.output_dir)),
                **metadata,
                "request": request,
                "attempt": attempt,
                "retry_errors": errors,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as error:
            message = str(error)
            for key in ("ELEVENLABS_API_KEY", "CARTESIA_API_KEY"):
                if os.getenv(key):
                    message = message.replace(os.environ[key], "[REDACTED]")
            errors.append(f"{type(error).__name__}: {message}")
            status = http_status(error)
            if status in (
                400,
                401,
                403,
                404,
            ):
                fatal = status in (401, 403, 404)
                break
            if attempt < args.attempts:
                time.sleep(min(30, 2**attempt))
    return {
        "id": row["id"],
        "status": "error",
        "run_id": run_id,
        "error": "; ".join(errors),
        "fatal": fatal,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", required=True, choices=SYSTEMS)
    parser.add_argument("--release-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-delivery", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="Maximum attempts per invocation; retries can incur additional charges",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rumik-endpoint", help="Your running Rumik-OSS 1 /v1/audio/speech endpoint"
    )
    parser.add_argument("--vertex-project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--vertex-location", default="global")
    args = parser.parse_args()
    if not 1 <= args.per_delivery <= 20 or args.workers < 1 or args.attempts < 1:
        parser.error("Use 1–20 prompts per delivery and positive workers/attempts")
    verify_release(args.release_dir)
    source = args.release_dir / f"artifacts/generation_manifests/{args.system}.jsonl"
    if (args.release_dir / "checksums.sha256").is_file():
        source = args.release_dir / f"requests/{args.system}.jsonl"
    original = unique_rows(read_jsonl(source), str(source))
    prompts = load_prompts(args.release_dir)
    if set(original) != {r["id"] for r in prompts}:
        raise ValueError("Archived request coverage differs from prompt set")
    counts, selected = Counter(), []
    for row in prompts:
        if counts[row["target_delivery"]] < args.per_delivery:
            selected.append(original[row["id"]])
            counts[row["target_delivery"]] += 1
    requests = {
        r["id"]: request_spec(args.system, r, args.rumik_endpoint) for r in selected
    }
    config = {
        "system": args.system,
        "dataset_revision": REVISION,
        "source_sha256": sha256(source),
        "inputs_sha256": hashlib.sha256(
            json.dumps(prompts, sort_keys=True).encode()
        ).hexdigest(),
        "generator_sha256": sha256(Path(__file__)),
        "requests": requests,
        "vertex_project": args.vertex_project,
        "vertex_location": args.vertex_location,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.output_dir / f"manifests/{args.system}.jsonl"
    config_path = args.output_dir / f"{args.system}_generation.json"
    with FileLock(str(args.output_dir / f".{args.system}.generation.lock"), timeout=0):
        if config_path.exists() and json.loads(config_path.read_text()) != config:
            raise ValueError(
                "Generation configuration changed; use a fresh output directory"
            )
        if not config_path.exists() and manifest.exists():
            raise ValueError("Existing manifest has no matching generation provenance")
        atomic_json(config_path, config)
        print(f"{len(selected)} requests; request preview: {config_path}")
        if args.dry_run:
            print("PASS: no generation API calls")
            return
        run_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        complete = (
            unique_rows(read_jsonl(manifest), str(manifest))
            if manifest.exists()
            else {}
        )
        for sample_id, row in complete.items():
            if row.get("run_id") != run_id or sample_id not in requests:
                raise ValueError("Existing generations belong to a different run")
            if (
                wav_info(args.output_dir / row["audio_path"])["audio_sha256"]
                != row["audio_sha256"]
            ):
                raise ValueError(f"Generated audio was modified: {sample_id}")
        pending = [r for r in selected if r["id"] not in complete]
        if pending:
            if args.system == "rumik_oss_1" and not args.rumik_endpoint:
                parser.error("--rumik-endpoint is required for generation")
            if args.system == "gemini":
                if not args.vertex_project:
                    parser.error("Set GOOGLE_CLOUD_PROJECT or --vertex-project")
                import google.auth

                google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            if args.system == "elevenlabs" and not os.getenv("ELEVENLABS_API_KEY"):
                parser.error("Set ELEVENLABS_API_KEY")
            if args.system.startswith("cartesia") and not os.getenv("CARTESIA_API_KEY"):
                parser.error("Set CARTESIA_API_KEY")
        # All providers write identical inputs; serialize shared metadata separately.
        with FileLock(str(args.output_dir / ".inputs.lock"), timeout=30):
            write_jsonl(
                args.output_dir / "inputs/code_switching_100.jsonl",
                [
                    {
                        "id": p["id"],
                        "type": f"{len(p['languages'])}-language:{p['language_sequence']}",
                        "prompt": p["control_prompt"],
                    }
                    for p in prompts
                ],
            )
            system_file = args.output_dir / "systems.json"
            system_list = [{"id": s, "name": SYSTEM_NAMES[s]} for s in SYSTEMS]
            if (
                system_file.exists()
                and json.loads(system_file.read_text()) != system_list
            ):
                raise ValueError("Output directory belongs to a different comparison")
            atomic_json(system_file, system_list)
        failed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(generate_one, args, row, requests[row["id"]], run_id)
                for row in pending
            ]
            for future in concurrent.futures.as_completed(futures):
                if future.cancelled():
                    failed += 1
                    continue
                row = future.result()
                if row.get("fatal"):
                    for queued in futures:
                        queued.cancel()
                failed += int(row["status"] != "ok")
                destination = (
                    manifest
                    if row["status"] == "ok"
                    else args.output_dir / f"errors/{args.system}.jsonl"
                )
                append_jsonl(destination, row)
                print(f"{args.system}/{row['id']}: {row['status']}", flush=True)
        if failed:
            raise SystemExit(
                f"{failed} generation failures; inspect errors and rerun the same command"
            )


if __name__ == "__main__":
    main()
