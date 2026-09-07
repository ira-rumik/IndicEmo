# IndicEmo Benchmark v1: Methodology

## 1. Scope

This benchmark evaluates how convincingly TTS systems express a requested delivery while synthesizing a single utterance that code-switches across two to five languages.

The principal question is:

> Given five recordings generated from the same code-switched transcript for the same requested delivery, which recording most genuinely, clearly, naturally, and consistently performs that delivery?

The benchmark compares complete inference configurations: model, voice policy, provider-supported controls, and a frozen provider adaptation. It does not claim to isolate model architecture from the inference interface or voice catalog.

## 2. Test set

The test set has 100 unique, original prompts. Each prompt includes:

- a spoken transcript;
- one target delivery: Happy, Sad, Angry, Excited, or Professional;
- one anchor accent: Hindi, Telugu, Tamil, Kannada, Bengali, Punjabi, or Indian English;
- one requested pace: Slow, Steady, or Fast;
- an ordered sequence of two to five languages.

### Distribution

| Dimension | Distribution |
| --- | --- |
| Delivery | 20 per label |
| Languages per utterance | 30 with 2, 25 with 3, 25 with 4, 20 with 5 |
| Anchor accent | Hindi 15, Kannada 15, all remaining conditions 14 each |
| Pace | Slow 17, Steady 51, Fast 32 |
| Unique language sequences | 79 |

The pace distribution reflects plausible delivery combinations. Professional is usually steady, Sad is often slow or steady, and Angry or Excited is often steady or fast. This benchmark does not claim factorial emotion–pace balance.

Professional is treated as a controlled speaking style, not an affective emotion. The release therefore publishes both the complete five-delivery table and a four-emotion table, plus a dedicated Professional scope, without presenting Professional as affect.

## 3. Prompt construction

The seven represented languages are English (`en`), Hindi (`hi`), Telugu (`te`), Tamil (`ta`), Kannada (`kn`), Bengali (`bn`), and Punjabi (`pa`). Each prompt contains meaningful clauses in multiple languages rather than isolated borrowed words.

Prompts were written to support an audible performance through concrete stakes, relationships, actions, and reactions. The four emotion labels include varied intensities: relief and delight for Happy, grief and restrained loss for Sad, confrontation and controlled fury for Angry, and anticipation or contagious enthusiasm for Excited. Professional prompts require composed authority and deliberate phrasing rather than flat neutrality.

The canonical control form is:

```text
<description="Happy, Hindi accent, Steady pace"> [spoken transcript]
```

The wrapper is metadata and is not part of the intended speech. Inline non-speech event tags are excluded from v1.

## 4. Evaluated systems

| Public system | Model/API identifier |
| --- | --- |
| rumik-oss 1 | rumik-oss 1 |
| Gemini 3.1 Flash TTS Preview | `gemini-3.1-flash-tts-preview` |
| ElevenLabs Eleven v3 | `eleven_v3` |
| Cartesia Sonic 3.5 | `sonic-3.5-2026-05-04` |
| Cartesia Sonic 3.6 | `sonic-preview`, invoked 2026-08-19 |

The `sonic-preview` alias is date-qualified because a mutable preview alias may later resolve to a different backend. Sonic 3.6 is the product name used in the figures; archived result files retain the original "Cartesia Sonic Preview" label and API identifier.

## 5. Fair provider adaptation

Every system receives the same intended spoken transcript and target delivery. Controls are translated into the provider's supported interface rather than sending one provider's private syntax to all systems.

- **rumik-oss 1:** receives the canonical description wrapper and transcript, with Ira as speaker, temperature 0.8, top-k 30, and maximum 2,048 new tokens.
- **Gemini:** receives a frozen natural-language director prompt specifying delivery cues, pace, multilingual continuity, exact transcript preservation, and no added speech or sound events.
- **ElevenLabs:** receives one supported delivery tag, one pace tag, and the unchanged transcript using Eleven v3.
- **Cartesia:** receives the unchanged transcript; emotion and speed are passed through structured generation controls. The mapping is Happy→`happy`, Sad→`sad`, Angry→`angry`, Excited→`excited`, Professional→`neutral`; speed is 0.8, 1.0, or 1.2 for Slow, Steady, or Fast.

One recipe is fixed per provider. There is no per-row sampling search or manual cherry-picking. Complete request metadata is retained in the release.

## 6. Voice policy

rumik-oss 1 uses **Ira for every one of the 100 prompts**, across all languages, accents, emotions, and paces. This is a single-speaker cross-lingual condition.

Each commercial provider uses one fixed, appropriate female voice per anchor-accent condition. The voice cannot change by prompt or target delivery. Cartesia Sonic 3.5 and Cartesia Sonic Preview share the same voice map.

This policy avoids exhaustive provider-wide voice search while representing each service through a documented, language-appropriate configuration. It remains a system-level comparison, and voice choice is an acknowledged component of each configuration.

## 7. Audio generation validation

The final generation set contains 500 files: 100 prompts × 5 systems. Every system has exactly one successful audio file for every prompt ID. The release builder verifies:

- exactly 100 unique prompt IDs;
- exactly 100 successful generations per system;
- exact prompt-ID coverage for every system;
- source-file SHA-256 equality with the generation manifest;
- Ira as the rumik-oss 1 speaker for every row.

The historical archive embedded audio in Parquet files. The standalone Git bundle contains text inputs, frozen requests, original audio hashes and durations, and numeric judgments. It does not redistribute audio or judges' free-text responses. Users generate new recordings with their own provider access or supply local recordings.

## 8. Blind comparative judge design

Three audio-capable models independently evaluate the recordings:

| Judge | Model |
| --- | --- |
| Gemini | `gemini-3.1-pro-preview` |
| Qwen | `qwen3.5-omni-plus` |
| OpenAI | `gpt-realtime-2.1` |

One judge case contains five matched audio clips for one prompt. Systems are hidden behind labels A–E. Label assignment is deterministically rotated across prompts and independently offset for each judge. Over the full 100 prompts, every system occupies each list position exactly 20 times per judge.

The judge sees the target delivery label and five anonymous audio recordings. It does not see system/provider identity, voice identity, transcript or translation, language or anchor-accent label, requested pace, or another judge's result. Withholding these fields reduces explicit metadata leakage. The spoken content remains audible, and voices may be recognizable, so this design cannot eliminate semantic or model-family bias.

## 9. Expression Quality

Expression Quality is an anchored 1–5 score for the audible realization of the requested delivery.

The judge assesses:

1. **Tone and pitch:** contour, warmth or darkness, pressure, softness, tension, audible smile, fragility, and phrase endings.
2. **Energy and dynamics:** activation, force, contrast, emphasis, escalation, restraint, and release.
3. **Timing and phrasing:** rhythm, momentum, pauses, hesitation, clipping, elongation, breath grouping, and continuity.
4. **Authenticity and sustainment:** whether the performance sounds genuine rather than mechanical or indiscriminately exaggerated, and whether it remains coherent through the full clip.

| Score | Fixed anchor |
| ---: | --- |
| 1 | Absent, contradictory, or effectively flat |
| 2 | Weak trace; mostly neutral, generic, or unconvincing |
| 3 | Recognizable but mild, mixed, mechanical, or inconsistent |
| 4 | Strong, natural, specific, convincing, and sustained |
| 5 | Exceptional, nuanced, authentic, controlled, and compelling throughout |

A score of 5 means the best-realized performance, not merely the loudest, fastest, highest-pitched, or most exaggerated performance.

The judge first scores every clip independently, then ranks all five. When score ties occur, it compares authenticity and specificity, sustainment, and purposeful natural variation. A genuine tie is permitted.

### Target anchors

- **Happy:** warmth, audible smile, buoyancy, affectionate or delighted phrasing, positive release, relief, pride, contentment, or joy.
- **Sad:** emotional weight, vulnerability, effortful or reduced energy, breathiness, hesitation, heaviness, fragile endings, grief, resignation, or restrained sorrow.
- **Angry:** vocal tension, pressure, sharp attacks, forceful stress, clipped phrasing, impatience, confrontation, escalation, or controlled fury.
- **Excited:** elevated activation, animated pitch movement, momentum, anticipation, delighted surprise, awe, urgency, or contagious enthusiasm.
- **Professional:** composed authority, confidence, precision, control, deliberate phrasing, measured reassurance, and purpose.

## 10. What the expression judge ignores

The judge is explicitly told not to score language or accent identity, pronunciation or transcript accuracy, translation or code-switching correctness, requested pace, speaker identity or voice attractiveness, recording fidelity except when an artifact prevents perception, or presumed system family.

Consequently, the consensus expression score is not an accent, pronunciation, ASR, WER/CER, or general audio-quality score. Multilingual text is the stress condition under which expression is judged. Separate language-fidelity and accent evaluations may be added without changing the primary metric.

## 11. Output validation and exclusions

Judge output must contain exactly five clip records, valid integer scores, acoustic evidence fields, a complete ranking consistent with the scores and declared ties, winner metadata, confidence, and a list of inaudible clips. Schema-invalid or inaudible cases are retried. Missing audio is never converted into a low expression score.

Valid cases:

- Gemini: 100/100;
- Qwen: 100/100;
- OpenAI: 98/100.

OpenAI did not return valid final cases for `cs_0046` and `cs_0098`. Both prompts are excluded from every strict three-judge aggregate. Both prompts and available numeric judgments remain in the bundle; recordings are not bundled.

The strict common set is therefore 98 prompts and 294 prompt–judge cases. It contains 79 emotion prompts and 19 Professional prompts. Across all valid outputs, the flattened release contains 1,490 clip judgments.

Evidence-scope warnings are retained rather than removed: 4 Gemini clip outputs, 12 Qwen clip outputs, and 0 OpenAI clip outputs are flagged.

## 12. Metrics and aggregation

- **Consensus Expression Score:** median of the three judge scores for each prompt and system, followed by a mean within each delivery and an equal macro-average across the deliveries in scope.
- **Arithmetic judge mean:** ordinary mean across judge scores, retained only as a secondary sensitivity statistic.
- **Mean rank:** mean tie-aware ordinal position; lower is better.
- **Win rate:** fraction of prompt–judge cases ranked first; tied winners split one unit of credit.
- **Pairwise win rate:** fraction of shared cases where one system outranks another, with ties worth 0.5.

The primary score uses the per-prompt median across the three judges. This is fixed before system aggregation and prevents one unusually high or low judge score from controlling a prompt's consensus value. Prompt medians are averaged within delivery; multi-delivery scopes give each delivery equal weight.

The archived secondary leaderboard includes confidence intervals computed with 10,000 stratified prompt-level bootstrap resamples. Those intervals belong to the archived secondary statistics; the headline consensus CSV does not include confidence intervals. Do not attach secondary-statistic intervals to the median-consensus table. Prompt is the resampling unit because all systems and judges share the same prompt conditions.

Published scopes include emotion-only, all deliveries, every individual delivery, per-judge sensitivity, and all system pairs. No undocumented composite score is used.

## 13. Interpretation and limitations

The benchmark supports conclusions about these five frozen configurations on the released 100-prompt test set. It does not establish universal superiority across every voice or provider setting, human MOS, independent architecture quality with voice and interface effects removed, accent authenticity, multilingual transcript fidelity, pace adherence, or generalization outside the represented prompts and language combinations.

Audio-capable model judges can have correlated preferences and model-family biases. The standalone release includes numeric per-clip judgments, per-judge aggregates, pairwise outcomes, and archived secondary statistics with prompt-bootstrap confidence intervals. Free-text judge evidence and failed response logs are not bundled. This limits independent inspection of those explanations: reproducing score arithmetic is not equivalent to validating the original audio judgments.

### Observed judge dependence

Gemini TTS received particularly favorable assessments from the Gemini judge. The bundled `benchmark/results/judge_sensitivity.csv` gives the following statistics for Gemini TTS across the same 98 tasks, including Professional:

| Judge | Mean expression score for Gemini TTS /5 | Gemini TTS first-place share |
| --- | ---: | ---: |
| Gemini | 4.73 | 89.8% |
| Qwen | 4.66 | 71.4% |
| OpenAI | 3.73 | 46.9% |

These are arithmetic task means for each judge, not the category-macro-averaged three-judge consensus in the headline table. First-place shares split credit for ties. They are sensitivity diagnostics, not additional headline metrics.

The Gemini judge's preference was more pronounced in comparative rankings, although Qwen assigned Gemini TTS a similarly high mean score. This raises a possible same-family preference concern, but does not establish its cause. The experiment does not test whether a judge can identify the generating model, and does not demonstrate that Gemini recognized or favored its own family. Genuine performance differences, judge-specific calibration, and correlated acoustic preferences can also explain the observed pattern.

System and voice identities were withheld, but blinding metadata does not guarantee that recognizable acoustic characteristics are absent. The available audio-capable models we could practically use for multilingual comparison limited the judge panel; the three providers should not be treated as a representative sample of all possible evaluators. There is no independent human calibration in this release. Taking the per-task median limits the influence of one extreme score but cannot remove shared biases. Scores remain unadjusted. Numeric judgments and per-judge results are bundled for independent score analysis; original recordings and free-text responses are not included in this Git release.

## 14. Relationship to prior work

The protocol is methodologically inspired by [InstructTTSEval](https://arxiv.org/abs/2506.16381), particularly its use of audio-capable judges and explicit criteria for instruction-conditioned speech. IndicEmo Benchmark is not a subset or translation of that dataset. It introduces original Indic code-switching texts, fixed categorical controls, and matched five-system listwise comparisons.

Additional references:

- [InstructTTSEval evaluator](https://github.com/KexinHUANG19/InstructTTSEval)
- [Audio-Aware LLMs as Judges for Speaking Styles](https://aclanthology.org/2025.findings-emnlp.25/)
- [EmergentTTS-Eval](https://arxiv.org/abs/2505.23009)
