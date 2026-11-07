# PROMOIQ final audit report

**Date:** 7 Nov 2026. **Scope:** the whole submission, checked against the Kuku brief (Problem 1, *Find the Promos*).

**Method.** I re-checked the outputs instead of trusting them. I:
- re-read the brief;
- re-parsed the raw screenplays with a separate parser (`evaluation/verify_against_source.py`, not PROMOIQ's) and checked every recommended promo against them;
- read every shortlisted promo's explanation;
- ran a second blind review;
- did a clean install from `requirements.txt` and ran the tests, pipeline and app from it;
- scanned the git history, tracked files and PDF for keys and screenplay text.

Every number below comes from a command run during this audit.

## Verdict

- **The mechanical guarantees hold.** All 50 episodes are processed. Every recommended cut is a real screenplay timestamp, no cut splits a line, every duration is 20–60 s, and every evidence quote is in its cited source block.
- **The tool runs from a clean install.** All 33 tests pass, and it reproduces the shortlist exactly.
- **The weak spot is editorial judgment, not timestamps.** A blind reviewer would post 3 of the 10 offline promos as cut, and would drop 5, including **#1**, which ends one line before its payoff.
- **The audit fixed four implementation errors** (§5) and corrected three inaccurate sentences in the docs.
- **Nothing in the repository exposes screenplay text or the API key.**

## 1. Requirements checklist

| Brief requirement | Status | Evidence |
|---|---|---|
| Find promos across all episodes | Done | 50/50 episodes, 3,727 events, 3,367 candidates; `outputs/final/run_metadata.json` |
| Per promo: episode and start/end of every segment, in order | Done | `outputs/final/ranked_promos.json` / `.csv`; verified against source (§2) |
| Hook, cliffhanger, one line on why it works | Done, but the "why" is weak | Present for all 10. The "why it works" line is generated from a template and generic; theme labels come from keywords (§4) |
| Rank them: which to post first, and why | Done | Ranked by quality after a diversity pass; PDF page 1 table; `editorial_report.md` |
| Check quality: where it picks badly | Done | `evaluation/failure_analysis.md` (25 cases), `evaluation_report.md`, blind reviews 1 and 2 |
| Solution doc: PDF, ≤ 2 pages | Done | `docs/PROMOIQ_solution.pdf`, 2 A4 pages (rendered and checked) |
| Doc covers problem and why, assumptions, how it works, scale, how you know it's good incl. failures | Done | PDF sections, in that order |
| Doc names the AI tools used and one place they got it wrong | Done | PDF "AI tools used" |
| Prototype link that opens for anyone | **Not done (yours)** | Repo is local only, with no remote. See §6 |
| Raw outputs folder, unedited, full pack | **Exists locally; not shared (yours)** | `outputs/` (git-ignored). See §8 |
| Walkthrough video (optional) | Not done (yours) | Script: `docs/walkthrough_script.md` |
| Free tiers only | Done | $0: offline default; Gemini free tier (80 calls) |
| Runs again on a new input | Done for this format | Folder and `.zip` input both tested (identical results). Another show is assumed to use the same screenplay layout |
| Model easy to swap | Done, partly tested live | Set by environment variables. Gemini was tested live; Anthropic and OpenAI-compatible are implemented but **never run against a real API** |
| Human in the loop | Done | Shortlist plus alternatives, with an editor check on video |

## 2. Verified against the raw screenplays

Checked by `python -m evaluation.verify_against_source --input data/input outputs/final/ranked_promos.json outputs/gemini/final/ranked_promos.json`:

- **Episodes.** 50 files, numbered 1–50, none missing. The independent parser counts 3,727 timestamped blocks, the same as PROMOIQ's 3,727 events.
- **Promos checked.** 20 shortlisted (offline 10, Gemini 10) and 7 held-back alternatives. 0 problems:
  - every segment start/end is a source timestamp (a block start/end or scene boundary);
  - the `mm:ss` strings equal the seconds;
  - no cut is inside a dialogue line or sound cue;
  - segments move forward in story time;
  - total and reported durations agree and are 20–60 s;
  - every evidence quote is in its cited block.
- **The verifier itself works.** I tampered with three promos: an invented +0.37 s start, a 30 s overrun, and a fake quote. It flagged all three and exited with code 1.
- **ZIP input.** Running on the original `.zip` gave 50 episodes, 3,727 events and an identical shortlist.

## 3. The offline shortlist, promo by promo

Blind review round 2 gave a fresh AI reviewer only the source text of each clip. System labels, scores and PROMOIQ's explanations were hidden, and offline and Gemini clips were shuffled together. It scored hook (first 3 s only), cliffhanger and standalone clarity on 1–5. It is the same model family as the build assistant and a single rater, **not a human**.

| # | Segments | Dur. | Source checks | Hook / Cliff / Standalone | Verdict | What the reviewer saw (checked against the source where noted) |
|---|---|---|---|---|---|---|
| 1 | E27 01:32.80–02:04.60 | 31.8 s | pass | 2 / 3 / 4 | drop | Ends on "You hear that?!", one line before the villain's twist (confirmed in source). The longer cut is in `boundary_counterfactuals`. |
| 2 | E28 00:37.70–01:15.30 | 37.6 s | pass | 5 / 4 / 4 | **post** | Cold open on a poisoning claim, ends on a question |
| 3 | E24 00:32.80–01:10.50 | 37.7 s | pass | 5 / 4 / 5 | **post** | The reviewer's #1: a strike at 0.0 s and a clear power dynamic |
| 4 | E32 01:11.20–02:02.80 | 51.6 s | pass | 4 / 2 / 3 | drop | Talky, with a dangling ending; repeats #5's taunt (confirmed) |
| 5 | E31 01:23.20–02:02.50 | 39.3 s | pass | 2 / 2 / 3 | drop | Opens reacting to an unseen line, ends on a put-down; theme mislabelled "romance" |
| 6 | E1 00:54.60–01:14.60 | 20.0 s | pass (at the 20 s limit) | 4 / 4 / 5 | **post** | Jilted at the altar; instantly clear |
| 7 | E36 01:00.80–01:45.80 | 45.0 s | pass | 2 / 4 / 3 | drop | Vague opening, off-screen exposition |
| 8 | E11 00:43.30–01:16.80 | 33.5 s | pass | 4 / 3 / 4 | maybe | Ends on a sneer, not a reveal |
| 9 | E42 00:17.90–00:30.40 + E44 00:59.00–01:10.50 | 24.0 s | pass | 4 / 3 / 2 | maybe | The splice is confusing to a stranger and may spoil a late twist |
| 10 | E23 01:02.00–01:24.50 | 22.5 s | pass | 2 / 3 / 2 | drop | Opens on a list of symptoms; an unexplained "her" |

**Hooks, mechanically.** Every promo has at least one dialogue line starting in its first 3 s, but a line is not always a hook. The reviewer rated 4 of 10 openings at 2/5: setup lines or reactions to something unseen. This is the known failure §5.

**Averages.**

| Shortlist (n=10) | Hook | Cliffhanger | Standalone | Post / maybe / drop |
|---|---|---|---|---|
| Offline | 3.4 | 3.2 | 3.5 | 3 / 2 / 5 |
| Gemini, before the audit gate | 3.0 | 4.1 | 3.7 | 5 / 2 / 3 |

Neither list dominates. Sources: `evaluation/results/round2/blind_review_ai_round2.json`; the full text is in `outputs/evaluation_round2/` (private).

## 4. Hallucinations, unsupported claims and weak recommendations

| Finding | Where | Status |
|---|---|---|
| The heuristic cold viewer gave a **camera direction** as "why it matters" (5 of 10 promos) and named an "X vs Y" conflict for any two speakers (for example a maid and her mistress) | `ranked_promos.json` → `cold_viewer` | **Fixed** (§5) |
| An ending that Gemini's own judge said **leaves no open question** still made the Gemini shortlist (#10) | Gemini run | **Fixed**: now a gate (§5) |
| Offline #1 cut one line before its payoff, because the scorer prefers a question mark (9.37) to an accusation (7.98) | Ranking | **Open**: documented (failure 20) and shown honestly in the PDF and walkthrough |
| The same payoff appears twice in each list, yet both report 0% near-duplicates | Duplicate detection | **Open** (failure 22) |
| "Why it works" lines are template text, and themes come from keywords (#5 "romance" for a dungeon taunt) | Explanations | **Open**: disclosed on PDF page 1 |
| 70 of 1,503 Gemini quotes are not verbatim (7 mostly absent) | Gemini annotation | Flagged in output; none silently trusted |
| The LLM cold viewer says "not standalone" for 15 of 19 but gives passing clarity scores | Gemini judges | **Open** (failure 17) |
| Doc errors: "Gemini pick is exactly the offline #1" (it runs 3.9 s longer); "#1 cuts as the villain turns the scene" (it cuts one line before) | Report, PDF, walkthrough | **Corrected** |

No invented timestamps or invented source quotes were found in any recommended promo.

## 5. Fixes made in this audit

Each fix was re-run and re-verified.

1. **Heuristic explanations** (`src/evaluation/judges.py`).
   - Stakes now come only from spoken lines, and camera directions are ignored.
   - A conflict is named only if it is a known antagonistic pair and both characters speak.
   - These are display fields only: shortlist, alternatives, candidates and gates are byte-identical after the change.
   - New test (fails on the old code).
2. **The judge's "no open question" verdict is now a gate** (`src/pipeline.py`). Before, it was computed and then outweighed by the generator's score.
   - Offline: 0 candidates affected.
   - Gemini, re-run fully from cache with 0 new calls: exactly one pick removed and replaced.
   - New test (fails on the old code).
3. **`requirements.txt`** named the legacy Gemini SDK. It now names `google-genai`, which the live run used.
4. **ZIP upload path check** (`app.py`). A string-prefix check let `../uploaded_show_evil/…` through; it now uses `Path.is_relative_to`.
5. **Docs.**
   - Three inaccurate sentences corrected.
   - Blind review round 2 and failure cases 20–25 added.
   - README: 33 tests, plus the verifier step.
   - PDF rebuilt, still 2 pages.
6. **New tool:** `evaluation/verify_against_source.py`, the independent source checker used above.

## 6. Critical issues you must act on before submitting

1. **The prototype link does not exist yet.** The repo has no remote. The brief says every link must open "for anyone who has it".
   - **Safe to make public:** the repo contains 0 verbatim screenplay lines of 6+ words, scanned across all 59 files to be committed and the PDF, and no key (checked in the files and the full git history).
   - **Still in it:** character names and a few very short quotes.
   - **If you prefer:** keep it private and add Kuku's reviewers instead.
2. **The raw outputs are not shared yet.** Share the folders listed in §8 by link. Never put them on GitHub: they hold full screenplay text.
3. **The audit changes are not committed.**
4. **Know the weak picks before the interview:**
   - #1 is cut one line early. The tool tested the longer cut and preferred the shorter, and I chose not to retune the scorer this late.
   - #5, #7 and #10 have weak openings.
   - #9 is a confusing splice.
   - Say plainly that both blind reviews were done by AI, not by humans.
5. **No human ratings exist.** The template is in `evaluation/human_eval/`.

## 7. Test results (actual)

| Where | Command | Result |
|---|---|---|
| Dev environment (Python 3.10.19) | `python -m pytest -q` | **33 passed** in 27.05 s |
| Clean venv, fresh `pip install -r requirements.txt`, final code | `python -m pytest -q` | **33 passed** in 27.72 s |
| Clean venv | `python -m src.pipeline` | Exit code 0; shortlist identical to `outputs/final` |
| Clean venv | `verify_against_source` on its own output | 0 problems |
| Clean venv | Streamlit `AppTest`: load, then press **Run analysis** | No exceptions; 50 episodes, 3,367 candidates, 1,243 validated, 11.4 s |
| Browser pane (clean venv, before the last fixes) | Run analysis, then the Ranked promos tab | Same 10 promos and durations |
| Raw LLM log | 80 entries vs on-disk cache | 80/80 identical, append-only, timestamps in order |
| Secrets | Key in tracked files, git history (476 KB), `outputs/` | 0 / 0 / 0 |

Not tested:
- A different show's screenplay format.
- The Anthropic and OpenAI-compatible providers against real APIs.
- File upload in the app's browser UI. The ZIP check was tested as a function.
- A first run on a machine with no Hugging Face cache. Expect a one-time ~90 MB model download, which needs internet.

## 8. How to run

```bash
python3 -m venv .venv && source .venv/bin/activate        # Python 3.10 used; needs >= 3.9
pip install -r requirements.txt
# put the episode .txt files under data/input/ (any subfolder), or pass --input path/to/show.zip
python -m pytest -q                                        # 33 tests, synthetic data only
python -m src.pipeline                                     # offline, about 15-20 s -> outputs/
python -m evaluation.verify_against_source --input data/input outputs/final/ranked_promos.json
streamlit run app.py                                       # localhost only
python -m src.pipeline --input path/to/new_show.zip --out outputs_new_show   # a new show
python -m evaluation.benchmark                             # baselines + metrics + blind sheets
```

For the optional Gemini path, put `GEMINI_API_KEY` in `.env`, then run `python -m src.llm.smoke`, then the batched command in the README. The free tier allows about 20 requests per day per model.

## 9. Where every deliverable is

| Deliverable | Location |
|---|---|
| Solution doc (submit) | `docs/PROMOIQ_solution.pdf` (built by `docs/build_solution_pdf.py`) |
| Prototype (link it) | The repository root: `README.md` (run steps), `src/`, `app.py`, `config.py`, `tests/` |
| Ranked promos (main result) | `outputs/final/ranked_promos.json`, `ranked_promos.csv`, `editorial_report.md`, `run_metadata.json` |
| Raw outputs: share these | `outputs/raw/` (parsed events, all 3,367 candidates, evaluation results), `outputs/processed/` (story bible, graph, timelines, validated and rejected sets), `outputs/final/`, `outputs/gemini/` (live LLM run incl. `raw/llm_responses.jsonl` and logs), `outputs/evaluation*/` (blind sheets and keys) |
| Not needed in the share | `outputs/history/` (earlier snapshots), `outputs/app_run/` (an older app run) |
| Evaluation | `evaluation/evaluation_report.md`, `evaluation/failure_analysis.md`, `evaluation/results/` |
| Walkthrough script | `docs/walkthrough_script.md` |
| This audit | `evaluation/final_audit_report.md` |
