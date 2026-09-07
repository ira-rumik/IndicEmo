"""Frozen v1 judge prompt construction and response validation."""

from __future__ import annotations
import re

from . import clients as base


ALLOWED_DELIVERIES = {"Happy", "Sad", "Angry", "Excited", "Professional"}


LINGUISTIC_REASONING = re.compile(
    r"\b(?:language|accent|pronunciation|transcript|translation|code[- ]?switch(?:ing)?|"
    r"Hindi|Telugu|Tamil|Kannada|Bengali|Punjabi|English)\b",
    re.IGNORECASE,
)


def result_tool() -> dict:
    clip_properties = {
        "clip_id": {"type": "string", "enum": list(base.LABELS)},
        "expression_quality_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "tone_pitch_evidence": {"type": "string"},
        "energy_dynamics_evidence": {"type": "string"},
        "timing_phrasing_evidence": {"type": "string"},
        "authenticity_and_sustainment": {"type": "string"},
        "main_limitation": {"type": "string"},
    }
    return {
        "type": "function",
        "name": "submit_emotion_evaluation",
        "description": "Submit the five-clip emotion-only comparative evaluation.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_delivery": {
                    "type": "string",
                    "enum": sorted(ALLOWED_DELIVERIES),
                },
                "invalid_clips": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(base.LABELS)},
                },
                "clips": {
                    "type": "array",
                    "minItems": 5,
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": clip_properties,
                        "required": list(clip_properties),
                        "additionalProperties": False,
                    },
                },
                "ranking_best_to_worst": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(base.LABELS)},
                    "minItems": 5,
                    "maxItems": 5,
                },
                "tie_groups": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(base.LABELS)},
                    },
                },
                "winner": {"type": "string", "enum": list(base.LABELS)},
                "winner_margin": {
                    "type": "string",
                    "enum": ["indistinguishable", "slight", "moderate", "clear"],
                },
                "winner_reason": {"type": "string"},
                "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": [
                "target_delivery",
                "invalid_clips",
                "clips",
                "ranking_best_to_worst",
                "tie_groups",
                "winner",
                "winner_margin",
                "winner_reason",
                "confidence",
            ],
            "additionalProperties": False,
        },
    }


def case_prompt(template: str, case: dict) -> str:
    return f"""{template}

EVALUATION CASE
- Requested vocal delivery: {case["target_delivery"]}

The five anonymous recordings follow as Clip A through Clip E. Listen to all five completely and judge only their audible expressive delivery."""


def validate_and_enrich(result: dict, case: dict) -> dict:
    from jsonschema import Draft202012Validator
    import json

    schema = json.loads(
        (base.ROOT / "schemas/emotion_comparative_5way_v1.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(result)
    if result.get("target_delivery") != case["target_delivery"]:
        raise ValueError(
            f"Judge returned target {result.get('target_delivery')!r}, expected {case['target_delivery']!r}"
        )
    invalid_clips = result.get("invalid_clips")
    if not isinstance(invalid_clips, list):
        raise ValueError("invalid_clips must be a list")
    if invalid_clips:
        raise ValueError(f"Judge failed to perceive clips: {invalid_clips}")
    clips = result.get("clips")
    if not isinstance(clips, list) or len(clips) != 5:
        raise ValueError("Expected exactly five clip results")
    by_label = {}
    for clip in clips:
        label = str(clip.get("clip_id", "")).replace("Clip ", "").strip()
        if label not in base.LABELS or label in by_label:
            raise ValueError(f"Bad clip_id: {label}")
        clip["clip_id"] = label
        score = clip.get("expression_quality_score")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise ValueError(f"{label} bad expression_quality_score: {score}")
        evidence = " ".join(
            str(clip.get(key, ""))
            for key in (
                "tone_pitch_evidence",
                "energy_dynamics_evidence",
                "timing_phrasing_evidence",
                "authenticity_and_sustainment",
                "main_limitation",
            )
        )
        if not evidence.strip():
            raise ValueError(f"{label} has empty evidence")
        for key in (
            "tone_pitch_evidence",
            "energy_dynamics_evidence",
            "timing_phrasing_evidence",
            "authenticity_and_sustainment",
            "main_limitation",
        ):
            if not clip[key].strip():
                raise ValueError(f"{label} has empty {key}")
        # The score/ranking remain usable because every provider synthesized the
        # same transcript. Preserve scope leakage as an auditable warning rather
        # than repeatedly discarding an otherwise valid judgment.
        clip["evidence_scope_warning"] = bool(LINGUISTIC_REASONING.search(evidence))
        clip["provider"] = case["blind_mapping"][label]
        by_label[label] = clip
    if set(by_label) != set(base.LABELS):
        raise ValueError("Missing or duplicate clip labels")
    reported_ranking = [
        str(x).replace("Clip ", "").strip()
        for x in result.get("ranking_best_to_worst", [])
    ]
    if len(reported_ranking) != 5 or set(reported_ranking) != set(base.LABELS):
        raise ValueError(f"Malformed ranking: {reported_ranking}")
    ranked_scores = [
        by_label[label]["expression_quality_score"] for label in reported_ranking
    ]
    if ranked_scores != sorted(ranked_scores, reverse=True):
        raise ValueError(
            f"Ranking contradicts scores: {list(zip(reported_ranking, ranked_scores))}"
        )
    if result.get("winner") != reported_ranking[0]:
        raise ValueError("Winner must be first in ranking")
    tied = set()
    for group in result["tie_groups"]:
        if tied.intersection(group):
            raise ValueError("Overlapping tie groups")
        tied.update(group)
        if len({by_label[label]["expression_quality_score"] for label in group}) != 1:
            raise ValueError("Tied clips must have equal scores")
        positions = sorted(reported_ranking.index(label) for label in group)
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("Tied clips must be adjacent in the ranking")
    confidence = result.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 1 <= confidence <= 5
    ):
        raise ValueError(f"Bad confidence: {confidence}")
    result["ranking_best_to_worst"] = reported_ranking
    result["winner_provider"] = case["blind_mapping"][reported_ranking[0]]
    result["target_delivery"] = case["target_delivery"]
    return result
