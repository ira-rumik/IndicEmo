#!/usr/bin/env python3
"""Verify bundled benchmark data and prepare inputs without network access."""

import argparse
import hashlib
import io
import json
import re
import wave
from pathlib import Path

import pyarrow.parquet as pq
from filelock import FileLock

from evaluation.io import atomic_json, sha256, systems_for, wav_info, SYSTEM_NAMES

# Historical provenance only; no Hugging Face client or download is used.
REVISION = "22cef1d402e3d7376ec7a42c80e0051742e08eb1"
BENCHMARK_DIR = Path(__file__).resolve().parent / "benchmark"
SYSTEMS = (
    "rumik_oss_1",
    "gemini",
    "elevenlabs",
    "cartesia_sonic_3_5",
    "cartesia_sonic_preview",
)


def verify_release(release: Path, audio: bool = False) -> None:
    root = release.resolve()
    bundled = (root / "checksums.sha256").is_file()
    if bundled and audio:
        raise ValueError(
            "The bundled benchmark contains no recordings. Generate audio with "
            "generate.py, or use --local-release with your own full audio archive."
        )
    manifest = root / ("checksums.sha256" if bundled else "artifacts/checksums.sha256")
    entries = {}
    for line in manifest.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or not (
            root / relative
        ).resolve().is_relative_to(root):
            raise ValueError("Invalid release checksum manifest")
        if relative in entries:
            raise ValueError("Duplicate release checksum entry")
        entries[relative] = digest
    required = [
        "data/prompts/test.parquet",
        "data/judgments/test.parquet",
        "results/consensus_scores.csv",
    ]
    if audio:
        required += [f"data/generations/{s}.parquet" for s in SYSTEMS]
    if bundled:
        required = ["prompts.jsonl", "judgments.jsonl", "results/consensus_scores.csv"]
        required += [f"requests/{s}.jsonl" for s in SYSTEMS]
    for relative in required:
        if relative not in entries or not (root / relative).is_file():
            raise ValueError(f"Pinned release is incomplete: {relative}")
    checked = 0
    for relative, digest in entries.items():
        path = root / relative
        if bundled and not path.is_file():
            raise ValueError(f"Bundled benchmark is incomplete: {relative}")
        if path.is_file():
            if sha256(path) != digest:
                raise ValueError(f"Release checksum mismatch: {relative}")
            checked += 1
    print(f"Verified {checked} archived files against the release checksum manifest")


def load_prompts(release: Path) -> list[dict]:
    path = release / "prompts.jsonl"
    if path.is_file():
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
    else:
        rows = pq.read_table(release / "data/prompts/test.parquet").to_pylist()
    ids = [row["id"] for row in rows]
    if len(ids) != 100 or set(ids) != {f"cs_{i:04}" for i in range(1, 101)}:
        raise ValueError("Expected the 100 canonical prompt IDs")
    return rows


def prepare_inputs(release: Path, output: Path) -> None:
    prompts = load_prompts(release)
    canonical_systems = [{"id": s, "name": SYSTEM_NAMES[s]} for s in SYSTEMS]
    system_file = output / "systems.json"
    if (
        system_file.exists()
        and json.loads(system_file.read_text()) != canonical_systems
    ):
        raise ValueError("Existing comparison has different system identities")
    write_jsonl(
        output / "inputs/code_switching_100.jsonl",
        [
            {
                "id": r["id"],
                "type": f"{len(r['languages'])}-language:{r['language_sequence']}",
                "prompt": r["control_prompt"],
            }
            for r in prompts
        ],
    )
    atomic_json(system_file, canonical_systems)
    print("Prepared 100 text inputs; no audio downloaded or generated")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise ValueError(
            f"Refusing to overwrite different data: {path}; choose a new output directory"
        )
    path.write_text(content, encoding="utf-8")


def unpack(release: Path, output: Path):
    prompts = load_prompts(release)
    ids = {row["id"] for row in prompts}
    if len(prompts) != 100 or len(ids) != 100:
        raise ValueError("Expected 100 unique prompts")
    if any(not re.fullmatch(r"cs_\d{4}", sample_id) for sample_id in ids):
        raise ValueError("Unexpected prompt ID")
    output = output.resolve()
    canonical_systems = [{"id": s, "name": SYSTEM_NAMES[s]} for s in SYSTEMS]
    system_file = output / "systems.json"
    if (
        system_file.exists()
        and json.loads(system_file.read_text()) != canonical_systems
    ):
        raise ValueError("Existing comparison has different system identities")
    write_jsonl(
        output / "inputs/code_switching_100.jsonl",
        [
            {
                "id": r["id"],
                "type": f"{len(r['languages'])}-language:" + r["language_sequence"],
                "prompt": r["control_prompt"],
            }
            for r in prompts
        ],
    )
    for system in SYSTEMS:
        rows = pq.read_table(release / f"data/generations/{system}.parquet").to_pylist()
        if len(rows) != 100 or {r["prompt_id"] for r in rows} != ids:
            raise ValueError(f"Incomplete prompt coverage: {system}")
        manifest = []
        for row in rows:
            audio = row["audio"]["bytes"]
            if hashlib.sha256(audio).hexdigest() != row["audio_sha256"]:
                raise ValueError(f"Audio checksum mismatch: {row['id']}")
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
                    1,
                    2,
                    24000,
                ) or wav.getnframes() == 0:
                    raise ValueError(
                        f"Expected nonempty mono PCM16 24 kHz WAV: {row['id']}"
                    )
            path = output / "audio" / system / f"{row['prompt_id']}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and sha256(path) != row["audio_sha256"]:
                raise ValueError(f"Refusing to overwrite different audio: {path}")
            path.write_bytes(audio)
            manifest.append(
                {
                    "id": row["prompt_id"],
                    "audio_path": str(path.relative_to(output)),
                    "status": "ok",
                    "audio_sha256": row["audio_sha256"],
                }
            )
        write_jsonl(output / "manifests" / f"{system}.jsonl", manifest)
        print(f"{system}: verified and unpacked {len(rows)} clips", flush=True)
    atomic_json(system_file, canonical_systems)


def register_comparison(release: Path, config_path: Path, output: Path):
    systems = json.loads(config_path.read_text())
    if not isinstance(systems, list) or len(systems) != 5:
        raise ValueError("Comparison JSON must be an ordered list of five systems")
    # Validate before writing any input or manifest files.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        probe = Path(directory)
        atomic_json(probe / "systems.json", systems)
        systems_for(probe)
    prompts = load_prompts(release)
    manifests = {}
    for system in systems:
        audio_dir = Path(system["audio_dir"])
        if not audio_dir.is_absolute():
            audio_dir = config_path.resolve().parent / audio_dir
        rows = []
        for prompt in prompts:
            audio = (audio_dir / f"{prompt['id']}.wav").resolve()
            rows.append(
                {
                    "id": prompt["id"],
                    "audio_path": str(audio),
                    "status": "ok",
                    **wav_info(audio),
                }
            )
        manifests[system["id"]] = rows
    output.mkdir(parents=True, exist_ok=True)
    canonical_systems = [{"id": s["id"], "name": s["name"]} for s in systems]
    system_file = output / "systems.json"
    if (
        system_file.exists()
        and json.loads(system_file.read_text()) != canonical_systems
    ):
        raise ValueError("Existing comparison has different system identities")
    for key, rows in manifests.items():
        write_jsonl(output / f"manifests/{key}.jsonl", rows)
    write_jsonl(
        output / "inputs/code_switching_100.jsonl",
        [
            {
                "id": r["id"],
                "type": f"{len(r['languages'])}-language:{r['language_sequence']}",
                "prompt": r["control_prompt"],
            }
            for r in prompts
        ],
    )
    atomic_json(system_file, canonical_systems)
    print(
        "Registered five named systems and verified all 500 files; audio was not copied"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Unpack recordings from a user-supplied legacy --local-release archive",
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=BENCHMARK_DIR,
        help="Local benchmark bundle (default: repository benchmark/)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--comparison",
        type=Path,
        help="Register five custom audio directories from an ordered JSON system list",
    )
    parser.add_argument(
        "--local-release",
        type=Path,
        help="Use an existing local archive instead of the bundled benchmark",
    )
    args = parser.parse_args()
    if args.audio and args.comparison:
        parser.error("Use either --audio or --comparison")
    release = args.local_release or args.release_dir
    print(f"Local benchmark: {release}")
    verify_release(release, audio=args.audio)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.output_dir / ".prepare.lock"), timeout=0):
        if args.audio:
            unpack(release, args.output_dir)
        elif args.comparison:
            register_comparison(release, args.comparison, args.output_dir)
        else:
            prepare_inputs(release, args.output_dir)


if __name__ == "__main__":
    main()
