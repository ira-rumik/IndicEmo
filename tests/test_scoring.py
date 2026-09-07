"""Offline checks for the consensus rule and incomplete judge coverage."""

import json
import tempfile
import unittest
from pathlib import Path

from aggregate import DELIVERIES, SYSTEMS
from summarize import summarize


class ConsensusTests(unittest.TestCase):
    def write_cases(self, root, missing_openai=False, bad_score=False):
        for judge, score in (("gemini", 1), ("qwen", 4), ("openai", 5)):
            rows = []
            for i, delivery in enumerate(DELIVERIES):
                for repeat in range(2):
                    if missing_openai and judge == "openai" and i == 0 and repeat == 0:
                        continue
                    mapping = dict(zip("ABCDE", [s for s, _ in SYSTEMS]))
                    rows.append(
                        {
                            "id": f"{i}_{repeat}",
                            "target_delivery": delivery,
                            "blind_mapping": mapping,
                            "result": {
                                "invalid_clips": [],
                                "clips": [
                                    {
                                        "clip_id": label,
                                        "expression_quality_score": True
                                        if bad_score
                                        else score,
                                    }
                                    for label in mapping
                                ],
                            },
                        }
                    )
            (root / f"{judge}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows)
            )

    def test_median_not_arithmetic_mean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_cases(root)
            rows, _ = summarize(root)
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(r["overall_consensus_score"] == 4 for r in rows))

    def test_common_intersection_excludes_for_every_system(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_cases(root, missing_openai=True)
            rows, missing = summarize(root)
            self.assertEqual(missing["openai"], ["0_0"])
            self.assertTrue(
                all(
                    r["strict_common_prompts"] == 9 and r["happy_prompts"] == 1
                    for r in rows
                )
            )

    def test_reject_boolean_scores(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_cases(root, bad_score=True)
            with self.assertRaises(ValueError):
                summarize(root)


if __name__ == "__main__":
    unittest.main()
