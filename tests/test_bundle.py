"""Check standalone data and workflows without HF, credentials, or API calls."""

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate
from aggregate import compute
from prepare import BENCHMARK_DIR, SYSTEMS, load_prompts, prepare_inputs, verify_release


class BundleTests(unittest.TestCase):
    def test_bundle_and_exact_scores(self):
        verify_release(BENCHMARK_DIR)
        with (BENCHMARK_DIR / "results/consensus_scores.csv").open() as source:
            expected = list(csv.DictReader(source))
        actual = compute(BENCHMARK_DIR / "judgments.jsonl")
        self.assertEqual(
            [{k: str(v) for k, v in row.items()} for row in actual], expected
        )
        self.assertEqual(len(load_prompts(BENCHMARK_DIR)), 100)

    def test_no_audio_or_response_prose(self):
        rows = [
            json.loads(line)
            for line in (BENCHMARK_DIR / "judgments.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows), 1490)
        self.assertFalse(
            any(
                p.suffix in {".wav", ".mp3", ".parquet"}
                for p in BENCHMARK_DIR.rglob("*")
            )
        )
        for row in rows:
            self.assertNotIn("tone_pitch_evidence", row)
            self.assertNotIn("audio", row)

    def test_bundled_audio_request_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "contains no recordings"):
            verify_release(BENCHMARK_DIR, audio=True)

    def test_missing_or_tampered_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "benchmark"
            shutil.copytree(BENCHMARK_DIR, root)
            requests = root / "requests/gemini.jsonl"
            original = requests.read_bytes()
            requests.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_release(root)
            requests.unlink()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                verify_release(root)

    def test_prepare_is_repeatable_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch(
                "socket.socket", side_effect=AssertionError("Network forbidden")
            ):
                prepare_inputs(BENCHMARK_DIR, output)
                prepare_inputs(BENCHMARK_DIR, output)
            rows = (output / "inputs/code_switching_100.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 100)
            self.assertFalse((output / "audio").exists())

    def test_all_provider_previews_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            for system in SYSTEMS:
                args = [
                    "generate.py",
                    "--system",
                    system,
                    "--per-delivery",
                    "20",
                    "--dry-run",
                    "--output-dir",
                    str(Path(directory) / system),
                ]
                with (
                    patch.object(sys, "argv", args),
                    patch(
                        "socket.socket", side_effect=AssertionError("Network forbidden")
                    ),
                    patch.object(
                        generate, "synthesize", side_effect=AssertionError("Paid call")
                    ),
                ):
                    generate.main()
                config = json.loads(
                    (Path(directory) / system / f"{system}_generation.json").read_text()
                )
                self.assertEqual(len(config["requests"]), 100)

    def test_cli_defaults_work_outside_repository(self):
        root = BENCHMARK_DIR.parent
        with tempfile.TemporaryDirectory() as directory:
            for args in [
                [str(root / "prepare.py")],
                [str(root / "aggregate.py"), "--check"],
            ]:
                subprocess.run(
                    [sys.executable, *args],
                    cwd=directory,
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
