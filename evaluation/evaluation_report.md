# PROMOIQ evaluation report

Every number here comes from `evaluation/results/benchmark.json` and `evaluation/results/round1/`. They are produced by `python -m evaluation.benchmark` and `python -m evaluation.review_summary`.

## Systems compared (top 10 promos each, same input, same metrics)

| System | What it does |
|---|---|
| PROMOIQ | Full pipeline: legal cut points, hook/cliffhanger/clarity objective, continuous enumeration + beam search, validator, judges, gates, diversity pass |
| A0 keyword window | No AI: picks the dialogue lines with the most dramatic keywords and cuts a fixed 30 s window around each |
| B event ranking | Ranks beats by PROMOIQ's own tension score and extends each to ≥ 30 s. Uses the same features, but no sequence construction, gates or diversity |
| A LLM-only | One prompt per episode with the raw script (`baselines.baseline_a_llm`). Run on Gemini for 28 of 50 episodes, reported in the live LLM section below |

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
2. Run Baseline A on all 50 episodes and LLM-judge 30+ candidates. Two days of free tier allowed 28 episodes and 19 candidates (see below).
3. Online: A/B test shortlisted promos against editor-picked ones on 3-second hold rate, completion and click-through to episode 1, and track how many shortlist picks editors keep.

## Live LLM run: Gemini free tier

The run took two days of free-tier quota (20 requests per day per model). Round 2 ran after the daily reset and reused every cached answer from round 1, so it paid only for new work.

**Setup.**
- Annotation: `gemini-3-flash-preview`, 5 episodes per call (10 calls) plus 1 story-bible call. These were made once, in round 1.
- Judges: `gemini-3.1-flash-lite`, a different model, judging 19 distinct candidates (38 calls: 9 candidates in round 1, 10 more in round 2).
- Baseline A: `gemini-3.6-flash`, which returned promos for 28 of 50 episodes:
  - Round 1 covered episodes 1–12.
  - Round 2 added episodes 13–28.
  - Episode 29's answer failed validation, and the retry hit the daily cap.
- Cost: 80 live calls logged, $0. Round 1 had 43 calls and took 17 min; round 2 had 37 calls and took 18 min. Most of the time went on 13 s pacing and 503 back-offs.

Sources: `results/gemini/`, `results/compare_offline_vs_llm.json`, `failure_analysis.md` §12–19. Round-1 outputs are kept in `outputs/history/`.

| | Offline (heuristic) | Gemini, round 1 | Gemini, round 2 (current) |
|---|---|---|---|
| Candidates passing every gate | 1,243 | 2,342 | 2,341 (inflated LLM scores, §16) |
| Rejected for no hook evidence in the first 3 s (code check) | 0 | 152 | 152 |
| Candidates judged by the LLM | — | 9 | 19 |
| Shortlist size | 10 | 5 | 10 |
| Episodes covered by the shortlist | 1, 11, 23, 24, 27, 28, 31, 32, 36, 42+44 | 28, 29, 31, 32 | 2, 3, 12, 23, 27, 28, 29, 30, 31, 32 |
| Shortlist near-duplicates | 0% | 20% | 0% |
| Picks shared with the offline shortlist | — | 0 of 5 | 1 of 10 (offline #1, E27) |
| Cold viewer: "not standalone" verdicts | 0% (proxy too lenient) | 8 of 9 | 15 of 19 (§17) |
| Quotes not in the cited beat | n/a | 70 of 1,503 (7 mostly absent) | same annotations |

**Baseline A (LLM-only)**

| | Top 10, round 1 (12 episodes) | Top 10, round 2 (28 episodes) | All 28 promos |
|---|---|---|---|
| Cuts on real screenplay timestamps | 100% | 100% | 100% |
| No line cut mid-way | 80% | 100% | 89% (3 cut a line) |
| Duration within 20–60 s | 90% | 50% | 79% (6 out of range) |

An LLM alone extracts timestamps well, but it does not respect the format. 5 of its 10 highest-scored promos run 61–86 s. PROMOIQ guarantees 100% on all three rows, and adds exhaustive search, multi-segment joins, gates and explanations.

**Reading.**
- **The LLM is a different judge, not a better-calibrated one.** With 19 judged candidates, the round-2 shortlist spreads over 10 episodes and has no near-duplicates. So most of round 1's single-arc concentration came from the small judged pool.
- **Overlap with the offline list is still small.** The one shared pick is the offline #1.
- **6 of the 10 picks still come from episodes 27–32.**
- **Only a human review of both shortlists can say which is better,** and that has not been done.
