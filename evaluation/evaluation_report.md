# PROMOIQ evaluation report

Every number here comes from `evaluation/results/benchmark.json` and `evaluation/results/round1/`. They are produced by `python -m evaluation.benchmark` and `python -m evaluation.review_summary`.

## Systems compared (top 10 promos each, same input, same metrics)

| System | What it does |
|---|---|
| PROMOIQ | Full pipeline: legal cut points, hook/cliffhanger/clarity objective, continuous enumeration + beam search, validator, judges, gates, diversity pass |
| A0 keyword window | No AI: picks the dialogue lines with the most dramatic keywords and cuts a fixed 30 s window around each |
| B event ranking | Ranks beats by PROMOIQ's own tension score and extends each to ≥ 30 s. Uses the same features, but no sequence construction, gates or diversity |
| A LLM-only | One prompt per episode with the raw script (`baselines.baseline_a_llm`). **Not run:** no API key was available |

## Automatic metrics

See `results/benchmark_table.md`.

**Objective rows** (valid for comparing systems):

| Metric | PROMOIQ | A0 | B |
|---|---|---|---|
| Cuts on real screenplay timestamps | 100% | 0% | 100% |
| No line of dialogue cut mid-way | 100% | 10% | 100% |
| Duration within 20–60 s | 100% | 100% | 100% |
| Near-duplicate rate | 0% | 20% | 10% |

**Scorer-based rows**, using PROMOIQ's own scorers (biased towards PROMOIQ):

| Metric | PROMOIQ | A0 | B |
|---|---|---|---|
| Hook pass | 100% | 20% | 100% |
| Cliffhanger pass | 100% | 20% | 40% |
| Standalone pass | 100% | 100% | 100% |
| Spoiler flagged | 0% | 20% | 30% |

The 100% standalone pass across all three systems shows that the offline clarity proxy does not discriminate. See `failure_analysis.md` §9.

## Blind review, round 1 (AI reviewer, not human)

Setup: 18 clips (PROMOIQ 8, B 5, A0 5) were shuffled with system labels hidden, plus 6 A/B pairs. They were rated 1–5 on hook, cliffhanger, standalone, emotional appeal and coherence.
- **Reviewer:** the AI coding assistant that built the system. It rated before opening the key, but it is not independent.
- **Imperfect blinding:** A0 clips are exactly 30.0 s and visibly cut mid-line.
- **Coverage:** the round rated the shortlist *before* the hook fix (`failure_analysis.md` §5).

| | Hook | Cliffhanger | Standalone | Emotion | Coherence | Overall | Clips with timestamp issues |
|---|---|---|---|---|---|---|---|
| PROMOIQ (n=8) | 3.38 | **4.00** | 3.50 | **4.25** | **4.62** | **3.95** | 0 |
| B event rank (n=5) | **4.20** | 2.60 | **3.60** | 4.00 | 4.40 | 3.76 | 0 |
| A0 keyword (n=5) | 2.00 | 2.40 | 3.40 | 3.40 | 3.20 | 2.88 | 5 |

**Pairwise** ("which would you post first to a viewer who has never seen the show?"):
- PROMOIQ won 5 of 6 pairs.
- The one loss was P1: PROMOIQ #1 (E27 accusation) vs B #1 (E3 rescue). The rescue is clearer to a stranger.
- The reviewer's choice agreed with the model's score in 5 of 6 pairs.

**Reading:** PROMOIQ's advantage is in endings (cliffhangers) and clean cuts. B's beat-ranking finds stronger *openings*, but it ends on resolutions. That's exactly what PROMOIQ's sequence objective is designed to fix, and it's why the hook scorer was revised.

## What a real validation would add
1. Human editors fill in `human_eval/human_ratings_TEMPLATE.csv` for the fresh blind sheet in `outputs/evaluation/`. Then run `python -m evaluation.review_summary <file>`.
2. Run Baseline A on all 50 episodes and LLM-judge 30+ candidates. The free tier only allowed 12 episodes and 9 candidates (see below).
3. Online: A/B test shortlisted promos against editor-picked ones on 3-second hold rate, completion and click-through to episode 1, and track how many shortlist picks editors keep.

## Live LLM run: Gemini free tier

**Setup.**
- Annotation: `gemini-3-flash-preview`, 5 episodes per call (10 calls) plus 1 story-bible call.
- Judges: `gemini-3.1-flash-lite`, a different model, judging 9 distinct candidates (18 calls).
- Baseline A: `gemini-3.6-flash`, 12 of 50 episodes, because the free tier allows 20 requests per day per model.
- Cost: 42 live calls, $0. Wall time 17 min; 503 overloads accounted for most of it.

Sources: `results/gemini/`, `results/compare_offline_vs_llm.json`, `failure_analysis.md` §12–19.

| | Offline (heuristic) | Gemini |
|---|---|---|
| Candidates passing every gate | 1,243 | 2,342 (inflated LLM scores, §16) |
| Rejected for no hook evidence in the first 3 s (code check) | 0 | 152 |
| Shortlist size | 10 | 5 (judge budget, §18) |
| Episodes covered by the shortlist | 1, 11, 23, 24, 27, 28, 31, 32, 36, 42+44 | 28, 29, 31, 32 |
| Shortlist near-duplicates | 0% | 20% |
| Cold viewer: standalone pass rate on judged candidates | 100% (proxy too lenient) | 1 of 9 by the LLM's own verdict (§17) |
| Quotes not in the cited beat | n/a | 70 of 1,503 (7 mostly absent) |

**Baseline A (LLM-only, top 10 of 12 episodes)**
- 100% of cuts are real screenplay timestamps.
- 80% have no line cut mid-way.
- 90% are within 20–60 s.

So an LLM alone extracts timestamps well. PROMOIQ adds guarantees (100% on all three), exhaustive search, multi-segment joins, gates and explanations.

**Reading.** The LLM is a different judge, not a better-calibrated one. Its picks don't overlap the offline shortlist. It also concentrates on one arc, which is partly caused by the small judged pool. A human review of both shortlists is the only way to say which is better, and that has not been done.
