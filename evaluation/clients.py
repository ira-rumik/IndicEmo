"""Audio transports for the IndicEmo Expression Quality protocol."""

from __future__ import annotations


import argparse
import base64
import json
import os
import re
import threading
import time
import wave
from pathlib import Path

import httpx


def result_tool():
    from .protocol import result_tool as tool

    return tool()


ROOT = Path(__file__).resolve().parent


PROVIDERS = (
    "rumik_oss_1",
    "gemini",
    "elevenlabs",
    "cartesia_sonic_3_5",
    "cartesia_sonic_preview",
)


LABELS = "ABCDE"


JUDGE_OFFSETS = {"gemini": 0, "qwen": 1, "openai": 2}


JUDGE_MODELS = {
    "gemini": "gemini-3.1-pro-preview",
    "qwen": "qwen3.5-omni-plus",
    "openai": "gpt-realtime-2.1",
}


DESCRIPTION = re.compile(
    r'^<description="(?P<delivery>Happy|Sad|Angry|Excited|Professional), '
    r'(?P<accent>[^,]+), (?P<pace>Slow|Steady|Fast) pace">\s*'
)


_WRITE_LOCK = threading.Lock()


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()


class JudgeDecodeError(ValueError):
    def __init__(self, message, response_text):
        super().__init__(message)
        self.response_text = response_text


def parse_json(text: str) -> dict:
    original = text
    if not isinstance(text, str):
        raise JudgeDecodeError("Judge returned no text", str(text))
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise JudgeDecodeError("Judge returned no JSON object", original)
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise JudgeDecodeError("Judge returned malformed JSON", original) from error


def audio_data_url(path: str) -> str:
    return "data:;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def http_status(error: Exception) -> int | None:
    """Read HTTP status across httpx, GenAI, and WebSocket exceptions."""
    for value in (
        getattr(getattr(error, "response", None), "status_code", None),
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    ):
        if type(value) is int and 100 <= value <= 599:
            return value
    return None


def judge_gemini(
    args: argparse.Namespace, prompt: str, case: dict
) -> tuple[dict, dict]:
    from google import genai
    from google.genai import types

    contents: list[str | types.Part] = [prompt]
    for label in LABELS:
        contents.extend(
            [
                f"Clip {label}",
                types.Part.from_bytes(
                    data=Path(case["audio"][label]).read_bytes(), mime_type="audio/wav"
                ),
            ]
        )
    with genai.Client(
        vertexai=True, project=args.vertex_project, location=args.vertex_location
    ) as client:
        response = client.models.generate_content(
            model=JUDGE_MODELS["gemini"],
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=result_tool()["parameters"],
                max_output_tokens=8192,
            ),
        )
    usage = (
        response.usage_metadata.model_dump(mode="json")
        if response.usage_metadata
        else {}
    )
    return parse_json(response.text), usage


def judge_qwen(args: argparse.Namespace, prompt: str, case: dict) -> tuple[dict, dict]:
    content = [{"type": "text", "text": prompt}]
    for label in LABELS:
        content.extend(
            [
                {"type": "text", "text": f"Clip {label}"},
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": audio_data_url(case["audio"][label]),
                        "format": "wav",
                    },
                },
            ]
        )
    payload = {
        "model": JUDGE_MODELS["qwen"],
        "messages": [{"role": "user", "content": content}],
        "modalities": ["text"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.1,
        "max_tokens": 5000,
    }
    pieces, usage = [], {}
    with httpx.stream(
        "POST",
        args.qwen_url,
        headers={
            "Authorization": f"Bearer {os.environ['QWEN_API_KEY']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                continue
            chunk = json.loads(data)
            if chunk.get("error"):
                raise RuntimeError(f"Qwen stream error: {chunk['error']}")
            if chunk.get("choices"):
                delta = chunk["choices"][0].get("delta", {})
                if isinstance(delta.get("content"), str):
                    pieces.append(delta["content"])
            if chunk.get("usage"):
                usage = chunk["usage"]
    return parse_json("".join(pieces)), usage


def receive_until(ws, wanted: set[str]) -> dict:
    deadline = time.monotonic() + 180
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Realtime response deadline exceeded waiting for {sorted(wanted)}"
            )
        ws.settimeout(remaining)
        event = json.loads(ws.recv())
        if event.get("type") == "error":
            raise RuntimeError(json.dumps(event, ensure_ascii=False))
        if event.get("type") in wanted:
            return event


def montage_pcm_base64(case: dict, silence_seconds: float = 0.75) -> tuple[str, str]:
    """Combine all comparison clips into one timestamped PCM montage."""
    silence = b"\x00\x00" * round(24_000 * silence_seconds)
    chunks: list[bytes] = []
    timeline: list[str] = []
    cursor = 0.0
    for index, label in enumerate(LABELS):
        with wave.open(case["audio"][label], "rb") as file:
            if (file.getnchannels(), file.getsampwidth(), file.getframerate()) != (
                1,
                2,
                24_000,
            ):
                raise ValueError(f"Unexpected WAV format: {case['audio'][label]}")
            frames = file.readframes(file.getnframes())
            duration = file.getnframes() / file.getframerate()
        start, end = cursor, cursor + duration
        timeline.append(f"Clip {label}: {start:.2f}s to {end:.2f}s")
        chunks.append(frames)
        cursor = end
        if index < len(LABELS) - 1:
            chunks.append(silence)
            cursor += silence_seconds
    return base64.b64encode(b"".join(chunks)).decode("ascii"), "\n".join(timeline)


def judge_openai(
    args: argparse.Namespace, prompt: str, case: dict
) -> tuple[dict, dict]:
    import websocket

    ws = websocket.create_connection(
        f"wss://api.openai.com/v1/realtime?model={JUDGE_MODELS['openai']}",
        header=[f"Authorization: Bearer {os.environ['OPENAI_API_KEY']}"],
        timeout=180,
    )
    try:
        receive_until(ws, {"session.created"})
        ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": JUDGE_MODELS["openai"],
                        "output_modalities": ["text"],
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24_000},
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
        )
        receive_until(ws, {"session.updated"})
        montage, timeline = montage_pcm_base64(case)
        montage_note = (
            "The single attached audio is a montage of all five clips separated "
            "by 0.75 seconds of silence. Use these exact boundaries:\n" + timeline
        )
        ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": montage_note},
                            {"type": "input_audio", "audio": montage},
                        ],
                    },
                }
            )
        )
        receive_until(ws, {"conversation.item.done"})
        ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["text"],
                        "instructions": prompt,
                        "tools": [result_tool()],
                        "tool_choice": "required",
                        "max_output_tokens": 5000,
                    },
                }
            )
        )
        response = receive_until(ws, {"response.done"})["response"]
        if response.get("status") != "completed":
            raise RuntimeError(
                f"Realtime response did not complete: {response.get('status_details')}"
            )
        calls = [
            item
            for item in response.get("output", [])
            if item.get("type") == "function_call"
        ]
        if len(calls) != 1:
            raise ValueError(f"Expected one function call, got {len(calls)}")
        return parse_json(calls[0]["arguments"]), response.get("usage", {})
    finally:
        ws.close()
