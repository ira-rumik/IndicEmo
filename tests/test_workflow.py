"""Offline integration tests: no provider credentials or network calls required."""

import argparse
import io
import json
import tempfile
import unittest
import wave
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

import generate
from aggregate import DELIVERIES
from evaluation import clients, protocol, runner
from evaluation.io import atomic_json, load_cases, wav_info
from prepare import write_jsonl
from summarize import summarize


def valid_response(delivery="Happy"):
    return {
        "target_delivery": delivery,
        "invalid_clips": [],
        "clips": [
            {
                "clip_id": label,
                "expression_quality_score": 3,
                "tone_pitch_evidence": "Warm phrase endings.",
                "energy_dynamics_evidence": "Gentle variation.",
                "timing_phrasing_evidence": "Connected phrasing.",
                "authenticity_and_sustainment": "Consistent delivery.",
                "main_limitation": "No audio distortion; expression remains mild.",
            }
            for label in "ABCDE"
        ],
        "ranking_best_to_worst": list("ABCDE"),
        "tie_groups": [list("ABCDE")],
        "winner": "A",
        "winner_margin": "indistinguishable",
        "winner_reason": "Similar delivery.",
        "confidence": 3,
    }


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        audio = self.data / "sample.wav"
        self.data.mkdir()
        audio.write_bytes(generate.pcm_wav(b"\x01\x00" * 240))
        prompts = [
            {
                "id": f"cs_{i + 1:04}",
                "type": "2-language:hi-en",
                "prompt": f'<description="{DELIVERIES[i % 5]}, Hindi accent, Steady pace"> Test speech.',
            }
            for i in range(100)
        ]
        write_jsonl(self.data / "inputs/code_switching_100.jsonl", prompts)
        for system in clients.PROVIDERS:
            write_jsonl(
                self.data / f"manifests/{system}.jsonl",
                [
                    {"id": p["id"], "status": "ok", "audio_path": "sample.wav"}
                    for p in prompts
                ],
            )
        self.args = argparse.Namespace(
            judge="qwen",
            generation_run=self.data,
            output_dir=self.root / "run",
            selection_file=None,
            only_id=[],
            per_delivery=1,
            workers=1,
            attempts=1,
            dry_run=False,
            judge_model=None,
            vertex_project=None,
            vertex_location="global",
            qwen_url="https://example.invalid/v1/chat/completions",
        )

    def test_rotations_balanced_and_blind_prompt(self):
        for judge in clients.JUDGE_MODELS:
            self.args.judge = judge
            cases = load_cases(self.args)
            for label in "ABCDE":
                self.assertEqual(
                    set(Counter(c["blind_mapping"][label] for c in cases).values()),
                    {20},
                )
            prompt = protocol.case_prompt("RUBRIC", cases[0])
            for hidden in ("Test speech", "Hindi", "Steady", "rumik_oss_1"):
                self.assertNotIn(hidden, prompt)

    def test_only_id_can_select_last_task(self):
        self.args.only_id = ["cs_0100"]
        self.assertEqual(
            runner.select_cases(self.args, load_cases(self.args))[0]["id"], "cs_0100"
        )

    def test_duplicate_manifest_ids_rejected(self):
        path = self.data / "manifests/gemini.jsonl"
        with path.open("a") as file:
            file.write(path.read_text().splitlines()[0] + "\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_cases(self.args)

    def test_truncated_wav_rejected(self):
        path = self.data / "sample.wav"
        path.write_bytes(path.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            wav_info(path)

    def test_evidence_mentioning_no_audio_distortion_is_valid(self):
        case = load_cases(self.args)[0]
        self.assertEqual(
            len(protocol.validate_and_enrich(valid_response(), case)["clips"]), 5
        )

    def test_missing_evidence_and_inconsistent_ties_rejected(self):
        case = load_cases(self.args)[0]
        for change in ("evidence", "ties"):
            response = valid_response()
            if change == "evidence":
                response["clips"][0]["tone_pitch_evidence"] = ""
            else:
                response["clips"][0]["expression_quality_score"] = 5
            with self.assertRaises(ValueError):
                protocol.validate_and_enrich(response, case)

    def test_resume_skips_successes_and_detects_changed_audio(self):
        def judge(args, prompt, case):
            return valid_response(case["target_delivery"]), {}

        with (
            patch.object(runner, "arguments", return_value=self.args),
            patch.dict("os.environ", {"QWEN_API_KEY": "test"}),
            patch.object(clients, "judge_qwen", side_effect=judge) as call,
        ):
            runner.main()
            self.assertEqual(call.call_count, 5)
            runner.main()
            self.assertEqual(call.call_count, 5)
            (self.data / "sample.wav").write_bytes(generate.pcm_wav(b"\x02\x00" * 240))
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                runner.main()
            self.assertEqual(call.call_count, 5)

    def test_failed_case_resumes_without_rejudging_successes(self):
        def first(args, prompt, case):
            if case["id"] == "cs_0001":
                raise RuntimeError("simulated provider failure")
            return valid_response(case["target_delivery"]), {}

        with (
            patch.object(runner, "arguments", return_value=self.args),
            patch.dict("os.environ", {"QWEN_API_KEY": "test"}),
            patch.object(clients, "judge_qwen", side_effect=first),
        ):
            with self.assertRaises(SystemExit):
                runner.main()
        with (
            patch.object(runner, "arguments", return_value=self.args),
            patch.dict("os.environ", {"QWEN_API_KEY": "test"}),
            patch.object(
                clients,
                "judge_qwen",
                side_effect=lambda a, p, c: (valid_response(c["target_delivery"]), {}),
            ) as call,
        ):
            runner.main()
            self.assertEqual(call.call_count, 1)

    def test_all_judge_failures_are_in_coverage(self):
        selected = runner.select_cases(self.args, load_cases(self.args))
        for judge in clients.JUDGE_MODELS:
            rows = [
                {
                    "id": c["id"],
                    "judge": judge,
                    "target_delivery": c["target_delivery"],
                    "blind_mapping": c["blind_mapping"],
                    "result": valid_response(c["target_delivery"]),
                }
                for c in selected
            ]
            rows.append({"id": "cs_0099", "judge": judge, "error": "failed"})
            write_jsonl(self.root / f"results/{judge}.jsonl", rows)
        _, report = summarize(self.root / "results", details=True)
        self.assertEqual(report["all_judges_missing"], ["cs_0099"])

    def test_three_judge_run_summarizes_and_rejects_modified_results(self):
        self.args.vertex_project = "offline-test"
        for judge in clients.JUDGE_MODELS:
            self.args.judge = judge
            with (
                patch.object(runner, "arguments", return_value=self.args),
                patch.dict(
                    "os.environ", {"QWEN_API_KEY": "test", "OPENAI_API_KEY": "test"}
                ),
                patch("google.auth.default", return_value=(None, "offline-test")),
                patch.object(
                    clients,
                    f"judge_{judge}",
                    side_effect=lambda a, p, c: (
                        valid_response(c["target_delivery"]),
                        {},
                    ),
                ),
            ):
                runner.main()
        directory = self.args.output_dir / "results"
        rows, report = summarize(directory, details=True)
        self.assertEqual(report["common_valid_tasks"], 5)
        self.assertTrue(all(r["overall_consensus_score"] == 3 for r in rows))
        path = directory / "gemini.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records[0]["result"]["clips"][0]["expression_quality_score"] = 5
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        with self.assertRaisesRegex(ValueError, "differs from raw response"):
            summarize(directory)

    def test_custom_names_and_mismatched_judge_conditions(self):
        # Shared dummy provenance makes this test independent of provider calls.
        import hashlib

        case = load_cases(self.args)[0]
        systems = [{"id": s, "name": "Custom " + s} for s in clients.PROVIDERS]
        ids = [f"cs_{i + 1:04}" for i in range(5)]
        configs = {}
        for judge in clients.JUDGE_MODELS:
            config = {
                "ids": ids,
                "conditions": {},
                "systems": systems,
                "prompt_sha256": "same",
                "schema_sha256": "same",
                "runner_version": "test",
            }
            configs[judge] = config
            atomic_json(self.root / f"{judge}_config.json", config)
            run_id = hashlib.sha256(
                json.dumps(config, sort_keys=True).encode()
            ).hexdigest()
            write_jsonl(
                self.root / f"results/{judge}.jsonl",
                [
                    {
                        "id": sample_id,
                        "run_id": run_id,
                        "target_delivery": DELIVERIES[i],
                        "blind_mapping": case["blind_mapping"],
                        "result": valid_response(DELIVERIES[i]),
                    }
                    for i, sample_id in enumerate(ids)
                ],
            )
        rows, _ = summarize(self.root / "results")
        self.assertTrue(all(r["system_name"].startswith("Custom ") for r in rows))
        configs["openai"]["conditions"] = {"different": True}
        atomic_json(self.root / "openai_config.json", configs["openai"])
        with self.assertRaisesRegex(ValueError, "different inputs"):
            summarize(self.root / "results")

    def test_montage_boundaries(self):
        import base64

        case = load_cases(self.args)[0]
        encoded, timeline = clients.montage_pcm_base64(case)
        self.assertEqual(len(base64.b64decode(encoded)), (240 * 5 + 18000 * 4) * 2)
        self.assertIn("Clip A: 0.00s to 0.01s", timeline)
        self.assertIn("Clip B: 0.76s to 0.77s", timeline)

    def test_gemini_transport_five_audio_parts(self):
        case = load_cases(self.args)[0]
        response = MagicMock(text=json.dumps(valid_response()))
        response.usage_metadata.model_dump.return_value = {"total_token_count": 123}
        with patch("google.genai.Client") as client:
            method = client.return_value.__enter__.return_value.models.generate_content
            method.return_value = response
            result, usage = clients.judge_gemini(self.args, "RUBRIC", case)
        self.assertEqual(result, valid_response())
        self.assertEqual(usage["total_token_count"], 123)
        request = method.call_args.kwargs
        self.assertEqual(request["model"], clients.JUDGE_MODELS["gemini"])
        self.assertEqual(len(request["contents"]), 11)
        self.assertEqual(request["config"].temperature, 0.1)
        for part in request["contents"][2::2]:
            self.assertEqual(part.inline_data.mime_type, "audio/wav")
            self.assertEqual(
                part.inline_data.data, (self.data / "sample.wav").read_bytes()
            )

    def test_qwen_transport_stream_and_audio_order(self):
        reply = MagicMock()
        reply.iter_lines.return_value = [
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"content": json.dumps(valid_response())}}]}
            ),
            'data: {"usage": {"total_tokens": 123}}',
            "data: [DONE]",
        ]
        with (
            patch.dict("os.environ", {"QWEN_API_KEY": "test"}),
            patch.object(httpx, "stream") as stream,
        ):
            stream.return_value.__enter__.return_value = reply
            result, usage = clients.judge_qwen(
                self.args, "RUBRIC", load_cases(self.args)[0]
            )
        self.assertEqual(result, valid_response())
        self.assertEqual(usage["total_tokens"], 123)
        payload = stream.call_args.kwargs["json"]
        self.assertEqual(payload["model"], clients.JUDGE_MODELS["qwen"])
        content = payload["messages"][0]["content"]
        self.assertEqual(
            [c["text"] for c in content[1::2]], [f"Clip {c}" for c in "ABCDE"]
        )
        self.assertEqual(len(content[2::2]), 5)

    def test_openai_transport_montage_and_cleanup(self):
        ws = MagicMock()
        events = [
            {"type": t}
            for t in ("session.created", "session.updated", "conversation.item.done")
        ]
        events.append(
            {
                "type": "response.done",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "arguments": json.dumps(valid_response()),
                        }
                    ],
                    "usage": {"total_tokens": 123},
                },
            }
        )
        ws.recv.side_effect = [json.dumps(e) for e in events]
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test"}),
            patch("websocket.create_connection", return_value=ws),
        ):
            result, usage = clients.judge_openai(
                self.args, "RUBRIC", load_cases(self.args)[0]
            )
        self.assertEqual(result, valid_response())
        self.assertEqual(usage["total_tokens"], 123)
        sent = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        self.assertEqual(sent[-1]["response"]["instructions"], "RUBRIC")
        self.assertEqual(sent[-1]["response"]["tool_choice"], "required")
        self.assertEqual(sent[1]["item"]["content"][1]["type"], "input_audio")
        ws.close.assert_called_once()

    def test_native_vertex_authentication_error_not_retried(self):
        from google.genai.errors import ClientError

        self.args.judge = "gemini"
        self.args.attempts = 5
        error = ClientError(403, {"error": {"message": "Forbidden"}})
        with patch.object(clients, "judge_gemini", side_effect=error) as call:
            result = runner.evaluate(
                self.args, "RUBRIC", load_cases(self.args)[0], "test"
            )
        self.assertTrue(result["fatal"])
        self.assertEqual(call.call_count, 1)

    def test_malformed_response_is_preserved(self):
        with self.assertRaises(clients.JudgeDecodeError) as caught:
            clients.parse_json('{"broken":')
        self.assertEqual(caught.exception.response_text, '{"broken":')

    def test_generation_replays_exact_request_and_seed(self):
        row = {
            "voice_id": "voice",
            "request": {"seed": 42, "text": "Exact text", "model_id": "eleven_v3"},
        }
        request = generate.request_spec("elevenlabs", row)
        args = argparse.Namespace(system="elevenlabs")
        reply = httpx.Response(
            200,
            content=b"\x00\x00" * 24,
            request=httpx.Request("POST", request["endpoint"]),
        )
        with (
            patch.dict("os.environ", {"ELEVENLABS_API_KEY": "fake"}),
            patch.object(httpx, "post", return_value=reply) as post,
        ):
            audio = generate.synthesize(args, request)
        self.assertEqual(post.call_args.kwargs["json"], row["request"])
        with wave.open(io.BytesIO(audio)) as wav:
            self.assertEqual(wav.getframerate(), 24000)

    def test_no_generation_retry_after_authentication_failure(self):
        args = argparse.Namespace(system="elevenlabs", attempts=3, output_dir=self.root)
        error = httpx.HTTPStatusError(
            "unauthorized",
            request=httpx.Request("POST", "https://example.invalid"),
            response=httpx.Response(401),
        )
        with patch.object(generate, "synthesize", side_effect=error) as call:
            result = generate.generate_one(args, {"id": "cs_0001"}, {}, "run")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
