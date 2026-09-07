"""Validated local data and run provenance shared by the command-line tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import wave
from importlib.metadata import version
from pathlib import Path

from .clients import DESCRIPTION, JUDGE_OFFSETS, LABELS, PROVIDERS

SYSTEM_NAMES = {
    "rumik_oss_1": "Rumik-OSS 1",
    "gemini": "Gemini 3.1 Flash TTS Preview",
    "elevenlabs": "ElevenLabs Eleven v3",
    "cartesia_sonic_3_5": "Cartesia Sonic 3.5",
    "cartesia_sonic_preview": "Cartesia Sonic Preview",
}


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON at {path}:{number}; preserve the file and repair the incomplete record before resuming"
            ) from error
        if not isinstance(row, dict):
            raise ValueError(f"Expected an object at {path}:{number}")
        rows.append(row)
    return rows


def unique_rows(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        sample_id = row.get("id")
        if not isinstance(sample_id, str) or not re.fullmatch(r"cs_\d{4}", sample_id):
            raise ValueError(f"Invalid task ID in {label}: {sample_id!r}")
        if sample_id in result:
            raise ValueError(f"Duplicate task ID in {label}: {sample_id}")
        result[sample_id] = row
    return result


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(obj, file, ensure_ascii=False, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)


def wav_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        if (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getcomptype(),
        ) != (1, 2, 24000, "NONE") or frames <= 0:
            raise ValueError(f"Expected nonempty mono PCM16 24 kHz WAV: {path}")
        if len(wav.readframes(frames)) != frames * 2:
            raise ValueError(f"Truncated WAV payload: {path}")
    return {"audio_sha256": sha256(path), "duration_seconds": frames / 24000}


def systems_for(run: Path) -> list[dict]:
    path = run / "systems.json"
    systems = (
        json.loads(path.read_text())
        if path.exists()
        else [{"id": key, "name": SYSTEM_NAMES[key]} for key in PROVIDERS]
    )
    if not isinstance(systems, list) or len(systems) != 5:
        raise ValueError("A comparison requires exactly five systems")
    ids = [s.get("id", "") for s in systems]
    if len(set(ids)) != 5 or any(
        not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in ids
    ):
        raise ValueError("System IDs must be unique lowercase identifiers")
    if any(
        not isinstance(s.get("name"), str) or not s["name"].strip() for s in systems
    ):
        raise ValueError("Each system requires a display name")
    return systems


def load_cases(args) -> list[dict]:
    run = args.generation_run.resolve()
    rows = read_jsonl(run / "inputs/code_switching_100.jsonl")
    inputs = unique_rows(rows, "inputs")
    if len(inputs) != 100:
        raise ValueError(
            f"Expected the complete 100-task input manifest, found {len(inputs)}"
        )
    systems = systems_for(run)
    providers = tuple(s["id"] for s in systems)
    manifests = {}
    for provider in providers:
        manifest = unique_rows(
            read_jsonl(run / f"manifests/{provider}.jsonl"), provider
        )
        if set(manifest) - set(inputs):
            raise ValueError(f"{provider}: unknown task IDs in the audio manifest")
        for row in manifest.values():
            if row.get("status") != "ok":
                raise ValueError(f"{provider}/{row['id']}: generation status is not ok")
            path = Path(row["audio_path"])
            row["resolved_path"] = str(path if path.is_absolute() else run / path)
        manifests[provider] = manifest
    cases = []
    for index, row in enumerate(rows):
        match = DESCRIPTION.match(row["prompt"])
        if not match:
            raise ValueError(f"Invalid control wrapper: {row['id']}")
        shift = (index + JUDGE_OFFSETS[args.judge]) % 5
        ordered = providers[shift:] + providers[:shift]
        mapping = dict(zip(LABELS, ordered, strict=True))
        cases.append(
            {
                "id": row["id"],
                "type": row["type"],
                "target_delivery": match["delivery"],
                "target_pace": match["pace"],
                "target_accent": match["accent"],
                "transcript": row["prompt"][match.end() :],
                "blind_mapping": mapping,
                "audio": {
                    label: manifests[provider].get(row["id"], {}).get("resolved_path")
                    for label, provider in mapping.items()
                },
            }
        )
    return cases


def environment_record() -> dict:
    root = Path(__file__).resolve().parents[1]
    sources = sorted(
        [root / "eval.py", root / "requirements.txt", *root.glob("evaluation/*.py")]
    )
    return {
        "python": sys.version.split()[0],
        "packages": {
            name: version(name)
            for name in (
                "httpx",
                "google-genai",
                "websocket-client",
                "jsonschema",
                "filelock",
            )
        },
        "source_sha256": {str(p.relative_to(root)): sha256(p) for p in sources},
    }
