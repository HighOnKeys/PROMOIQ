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
2. Run Baseline A and the LLM judges with a key, ideally using a different model as the judge.
3. Online: A/B test shortlisted promos against editor-picked ones on 3-second hold rate, completion and click-through to episode 1, and track how many shortlist picks editors keep.
