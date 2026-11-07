# PROMOIQ

**Narrative-aware promo discovery for microdrama screenplays.** Kuku AI Generalist assignment, Problem 1: *Find the Promos (Ads)*.

PROMOIQ reads a show's screenplays and returns a ranked shortlist of 20–60 s promos. Each promo comes with:
- every segment's episode and exact start/end timestamps, in order
- the hook (first 3 s), the cliffhanger, and one line on why it works
- scores, source evidence, and quality checks
- the candidates it rejected, and why

It runs fully offline in about 20 s for the 50-episode pack. Any LLM can be plugged in without code changes.

> PROMOIQ analyses screenplay text only. It cannot verify actual video frames, acting performance, editing quality or audience engagement.

## Submission

Kumar Manas, roll no. 23119016, kumar_m@me.iitr.ac.in

| Item | Where |
|---|---|
| Solution doc (2 pages) | [`docs/PROMOIQ_solution.pdf`](docs/PROMOIQ_solution.pdf) |
| Prototype | this repository: run steps below, editorial app in `app.py` |
| Raw outputs on the full pack | shared through the submission form only. They contain Kuku's screenplay text, so they are not in this public repo |
| Quality evidence | [`evaluation/evaluation_report.md`](evaluation/evaluation_report.md), [`evaluation/failure_analysis.md`](evaluation/failure_analysis.md), [`evaluation/final_audit_report.md`](evaluation/final_audit_report.md) |

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate     # Python 3.9+ (built and tested on 3.10)
pip install -r requirements.txt                       # first run also downloads the ~90 MB MiniLM model
```

1. Put the episode `.txt` files in `data/input/`. Any subfolder works, and a `.zip` can be passed with `--input`. This folder is git-ignored.
2. Run the pipeline:
   ```bash
   python -m src.pipeline                      # full pipeline -> outputs/
   ```
3. Run the tests:
   ```bash
   python -m pytest -q                         # 33 tests (synthetic fixtures, no Kuku content)
   ```
4. Compare against the baselines and build the blind review sheets:
   ```bash
   python -m evaluation.benchmark              # baselines + metrics + blind review sheets
   ```
5. Re-check every recommended promo against the raw screenplays. This uses its own parser, not PROMOIQ's:
   ```bash
   python -m evaluation.verify_against_source --input data/input outputs/final/ranked_promos.json
   ```
   It checks that every cut is a real timestamp, that no line is split, that durations are 20–60 s, and that every evidence quote is in its cited block. It exits with code 1 on any problem.
6. Open the editorial app:
   ```bash
   streamlit run app.py                        # local editorial UI (bound to localhost)
   ```

To run on a new show, point `--input` at its folder or ZIP:

```bash
python -m src.pipeline --input path/to/new_show.zip --out outputs_new_show
```

No code or config changes are needed, as long as the files follow the same screenplay format: `**SPEAKER:**` blocks with `Start: (MM:SS.ss) | End: (MM:SS.ss)`. Case and spacing variations are tolerated.

## Swapping the model

The default `PROMOIQ_PROVIDER=heuristic` needs no key and costs nothing. To use an LLM, set environment variables (see `.env.example`):

```bash
export PROMOIQ_PROVIDER=gemini GEMINI_API_KEY=...            # or anthropic / openai_compat
export PROMOIQ_JUDGE_PROVIDER=anthropic ANTHROPIC_API_KEY=... # optional: a different model judges
python -m src.pipeline
```

`openai_compat` works with OpenAI, Groq, OpenRouter, Together, or a local vLLM/Ollama server (`OPENAI_BASE_URL`). Only the Gemini provider has been run against a real API. The Anthropic and OpenAI-compatible clients are implemented but have never been run against a real API. The shared gateway they use is tested with a fake provider.

Every model call goes through `src/llm/client.py`, which handles:
- **Caching:** results are stored on disk, so re-runs are free.
- **Retries:** failed calls are retried.
- **Raw logging:** responses are written unedited to `outputs/raw/llm_responses.jsonl`.
- **Usage counts.**
- **Validation:** outputs are checked with pydantic. Quotes the model cites are checked against the screenplay, and unverifiable ones are flagged.

**Live status:** the LLM path has been run on the full pack with a free Gemini key: 80 calls over two days, $0. This covers 19 LLM-judged candidates, a 10-promo Gemini shortlist, and Baseline A on 28 episodes. See `evaluation/evaluation_report.md` and failure cases 12–19 and 21 in `evaluation/failure_analysis.md`. Free-tier keys allow about **20 requests per day per model**, so use the batched settings:

```bash
python -m src.llm.smoke     # one call: checks the key, lists the models it can use
PROMOIQ_PROVIDER=gemini PROMOIQ_MODEL=gemini-3-flash-preview \
PROMOIQ_JUDGE_PROVIDER=gemini PROMOIQ_JUDGE_MODEL=gemini-3.1-flash-lite PROMOIQ_JUDGE_BUDGET=9 \
PROMOIQ_LLM_EPISODES_PER_CALL=5 python -m src.pipeline --out outputs/gemini
```

That is the day-1 command. On the next day, raise `PROMOIQ_JUDGE_BUDGET` (it was 19 on day 2). Cached answers are reused, so only the new candidates cost calls.

The client handles free-tier conditions:
- **Pacing:** calls are spaced 13 s apart (`PROMOIQ_MIN_CALL_INTERVAL_S`).
- **Waits:** it backs off on per-minute 429 and 503 errors, and stops at once on a per-day quota.
- **Caching:** finished calls are cached, so a re-run only redoes failed calls.

## How it works

```
parse ─► beats ─► dramatic features ─► story bible + graph ─► search ─► validate ─► judge ─► gates ─► MMR shortlist
```

1. **Screenplay intelligence** (`src/parser`)
   - A deterministic, case-tolerant parser reads 3,727 events: camera, action, dialogue and sound.
   - Timestamps are converted to seconds in code, never by an LLM.
   - Overlapping layers are merged into 1,633 **beats**. A promo may only cut at a camera or scene boundary, and only where no line of dialogue or sound cue would be sliced. So every cut point **is** a real screenplay timestamp.
2. **Dramatic intelligence** (`src/dramatic`)
   - Each event and beat is scored 0–5 on surprise, emotion, conflict, stakes, reveal, visual potential, curiosity and narrative importance.
   - Signals come from delivery cues (e.g. *Outraged, Accusing*), wording, camera language, sound cues, and the writers' own loglines.
   - Per-episode tension curve: `T = 0.30E + 0.30C + 0.25S + 0.15U`.
   - Every legal start point gets a hook strength (its first 3 s); every legal end point gets a cliffhanger strength.
3. **Narrative intelligence** (`src/narrative`)
   - Characters and aliases, relationships with evidence, and antagonistic conflicts. A pair counts as a conflict only when their own lines are more hostile than warm.
   - Conflict stages: INTRODUCED, DISCOVERED, CONFRONTED, ESCALATED, RESOLVED.
   - A NetworkX graph: 231 nodes, 579 typed edges.
   - Targeted context retrieval per promo, instead of re-sending all 50 scripts.
4. **Search** (`src/construction`)
   - **Continuous promos:** every legal 20–60 s window is enumerated exactly (fast, nothing missed).
   - **Multi-segment promos:** a constrained beam search over a library of 450 strong moments. Joins must move forward in story time, share a rival/ally or a conflict, stay within 60 s, and not retell the same moment. Partial sequences are ranked with an optimistic cliffhanger estimate.
5. **Objective**
   - `Q = 0.20H + 0.20K + 0.20C + 0.15E + 0.15G + 0.10N − 0.15R − 0.20S`, each component on 0–10.
   - Components: hook, cliffhanger, standalone clarity, emotional progression, information gap, coherence, redundancy, spoiler.
   - All weights live in `config.py`. They are hypotheses, not tuned values.
6. **Evaluation** (`src/evaluation`)
   - A Python hard validator: duration, real timestamps, no mid-line cuts, order, hook evidence, traceable evidence.
   - A cold-viewer judge that sees only the promo.
   - A cliffhanger/integrity judge with story context and a spoiler taxonomy.
   - Editorial gates; hard failures can never be outweighed by high scores.
7. **Ranking** (`src/ranking`)
   - Duplicate detection: time overlap, shared beats, same scene, and calibrated sentence-transformer similarity.
   - An MMR diversity pass chooses who makes the shortlist; quality decides the posting order.
   - Boundary counterfactuals (earlier/later open and close).
   - Strong alternatives that were held back are listed for the editor.

## Outputs (`outputs/`, private)

| Folder | Contents |
|---|---|
| `raw/` | `parsed_events.json`, `dramatic_events.json`, `promo_candidates.json` (all 3,367), `evaluation_results.json`, `llm_responses.jsonl` (when an LLM is used) |
| `processed/` | `story_bible.json`, `character_index.json`, `conflict_timeline.json`, `narrative_graph.json`, `episode_timelines.json`, `tension_profiles.json`, `hook_candidates.json`, `cliffhanger_candidates.json`, `validated_promos.json`, `rejected_candidates.json`, `duplicate_flags.json`, `parser_validation_report.json` |
| `final/` | `ranked_promos.json`, `ranked_promos.csv`, `editorial_report.md`, `run_metadata.json` |

## Evaluation

The benchmark compares the systems on one set of metrics. Results are in `evaluation/results/`:
- **PROMOIQ.**
- **A0:** a keyword-density fixed window, with no AI.
- **B:** independent event ranking, using PROMOIQ's own features but no sequence reasoning.
- **A:** LLM-only extraction, run on Gemini for 28 of 50 episodes (`evaluation/results/gemini/`).

Blind review round 1 (18 clips, 6 pairs) was rated by the AI coding assistant. It is **not** a human rating, and the reviewer built the system. Round 2, in the final audit, gave the offline and Gemini top 10s to a fresh AI reviewer. That reviewer saw only the source text, with labels and explanations hidden; it is still not human. Results are in `evaluation/evaluation_report.md` and failure cases 20–25. See `evaluation/human_eval/` for a template to collect human ratings. Failures and fixes are in `evaluation/failure_analysis.md`.

## Confidentiality

The input pack is Kuku's content. `data/`, `outputs/`, `*.zip` and the brief PDF are git-ignored. The app is bound to `localhost` with telemetry off. The tests use synthetic scripts only. Do **not** deploy the app publicly with the real pack. Share `outputs/` with Kuku through an unlisted "anyone with the link" folder (the brief asks for links that open for anyone who has them), never through a public repository.

## Project layout

```
config.py              all weights, gates, search and model settings
src/parser/            timestamps, screenplay parser, beats, validation report
src/dramatic/          lexicons, event/beat features (heuristic + LLM), timeline (tension, hooks, cliffs)
src/narrative/         characters, relationships, conflicts, graph, context retrieval
src/construction/      Show index, objective Q(P), continuous enumeration + beam search
src/evaluation/        hard validator, cold-viewer and integrity judges
src/ranking/           duplicates, MMR shortlist, counterfactuals, markdown report
src/llm/               provider-agnostic gateway (cache, retries, raw log, usage)
src/pipeline.py        end-to-end orchestration (CLI)
app.py                 Streamlit editorial UI
evaluation/            baselines, benchmark, source verifier, review summary, failure analysis, final audit, human-eval templates
docs/                  2-page solution PDF (+ builder), walkthrough script
tests/                 unit + end-to-end tests
```
