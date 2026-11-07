# PROMOIQ failure analysis

Every failure below happened during this build on the real 50-episode pack. Snapshots of the
affected runs are kept privately in `outputs/history/run1..run5_reviewed/` (they contain
screenplay excerpts, so they are not in the repository). Quotes are kept short on purpose.

How failures were found:
- (M) **Manual inspection**: I read the full text of every shortlisted promo after each run.
- (B) **Blind review**: round 1, 18 clips and 6 pairs with system labels hidden.
- (T) **Tests and measurement.**
- (A) **Final audit**: an independent re-check against the raw files, plus blind review round 2 (an AI reviewer, see `evaluation_report.md`).

| # | Failure | Found by | Status |
|---|---|---|---|
| 1 | Allies and lovers labelled as "conflicts" | M | Fixed; residual case below |
| 2 | Verbal scenes themed as "action" | M | Fixed |
| 3 | Multi-segment promos joined unrelated storylines through the protagonist | M | Fixed + new gate |
| 4 | Joined segment opened mid-sentence | M | Fixed |
| 5 | Hook scorer over-rewarded bare reaction lines ("What?") | B | Rule added; not re-tested blind |
| 6 | Semantic-similarity noise distorted the diversity pass | T | Calibrated from measured distribution |
| 7 | Diversity pass held back the reviewer's preferred promo | B | Not changed (judgment call); now surfaced to the editor |
| 8 | Cold-viewer judge missed an off-screen "she" | B | Open: needs coreference / LLM judge |
| 9 | Standalone-clarity proxy passes everything, including baselines | T | Open: reported, not hidden |
| 10 | Episode numbers are not story chronology | M | Open: documented assumption |
| 11 | Local app listened on the whole network | M | Fixed |

---

## 1. Conflict detector labelled allies as antagonists (the AI tool's mistake)

**What it produced.** In run 1, the biggest "conflict" in the show was **Alexander vs Juliana**, with 32 events. They are the romantic leads. **Juliana vs Prudence** (her loyal friend) and two pairs of co-conspirators were also listed. These labels then leaked into the "why it works" lines. For example, the E23 poison scene was explained as "conflict JULIANA vs PRUDENCE continues after the cut".

**Why it was wrong.** The code treated any two named characters who appear together in a tense scene as rivals. Allies share tense scenes all the time.

**How it was detected.** I read the "why it works" line of every promo against its script text. The claim contradicted the scene in front of me.

**Fix.** `pair_stance()` in `src/narrative/story.py` now measures what the two characters say to each other:
- **Hostility:** confrontation, betrayal and threat language, plus hostile delivery cues.
- **Warmth:** romance and rescue language, friendly phrases, and warm delivery cues.

A pair counts as a conflict only if hostility ≥ 3 and hostility ≥ 1.5 × warmth. After the fix, run 2 found Catherine–Juliana (26 events), Gerald–Juliana (13) and Clarissa–Juliana (6), and dropped Alexander–Juliana and Juliana–Prudence.

**Residual.** Catherine–Rupert and Clarissa–Gerald are still listed. They mock Juliana *together*, and hostility is measured per speaker, not per addressee. Fixing this needs addressee detection (an LLM task).

**This is the genuine AI-tool mistake.** The first version of this logic was written by the AI coding assistant (Claude Code) and looked plausible. The error only became visible by reading outputs against the source.

## 2. Purely verbal scenes themed as "action"
- **What it produced.** Run 1 labelled 5 of 10 promos "action", including a dungeon argument with no physical action.
- **Why it was wrong.** Camera directions mention props ("chains", "iron poker"), and those counted as physical action.
- **Fix.** `physical_action` is now counted only from ACTION and SOUND blocks. A separate "confrontation" theme was added, and "revenge" now requires explicit revenge language.
- **Result.** The final shortlist covers 6 angles.
- **Residual.** The E31 dungeon argument about love is themed "romance", which is debatable.

## 3. Multi-segment promos joined unrelated storylines
- **What it produced.** The top multi-segment candidates combined an E15 constable arrival with the E23 poison discovery (coherence 3.7–5.1 out of 10).
- **Why it was wrong.** The beam search accepted any join where the two moments shared a character. The protagonist speaks in nearly every scene, so almost anything could be joined to anything.
- **Fix.**
  - The protagonist is excluded from the "shared character" test, so joins need a shared rival or ally, or a shared conflict.
  - The beam keeps at most 3 states per opening moment, to stop it collapsing onto one opener.
  - A new gate rejects multi-segment promos with coherence below 5 (5 rejected in the final run).
- **Result.** The surviving multi-segment pick (E42 → E44, the wedding confrontation) is one continuous storyline.

## 4. A joined segment started mid-sentence
- **What it produced.** In run 1, PROMO-08's third segment opened on a line beginning "...but you have never learned".
- **Fix.** Lines starting with an ellipsis or a lowercase letter now count as "mid-conversation" openers. Any internal segment that starts this way loses 1.5 coherence points.

## 5. Hook scorer over-rewarded bare reaction lines
- **What it produced.** PROMOIQ's hooks averaged **3.38/5** in the blind review, versus **4.2** for the plain event-ranking baseline. The two lowest hook ratings (2/5) were promos that open on one-word or very short reactions ("What?", "What is it?"). These only work if you saw what they react to.
- **How it was detected.** The blind review notes, then confirmed by reading the hook scorer.
- **Fix.** In `EpisodeTimeline._hook`, a short question opener (3 words or fewer) with no conflict, threat or reveal language, and no physical or loud event in the first 3 seconds, now has its hook score multiplied by 0.7.
- **Caveat.** This was tuned after seeing the review, and the new shortlist was **not re-reviewed blind** (I would be biased). A fresh blind sheet for a human reviewer is generated in `outputs/evaluation/`.

## 6. Semantic similarity was mostly noise
- **What it produced.** The first MMR (diversity) pass penalised promos for sharing vocabulary. Everything in the show shares the same names and settings.
- **How it was detected (measured).** Over 150 top candidates:
  - Unrelated promos from different episodes: median raw cosine 0.38, 90th percentile 0.58, 99th percentile 0.71.
  - Time-overlapping duplicates: median 0.80.
  - The known E1/E4 altar retelling: 0.745.
- **Fix.** Similarity is rescaled from a baseline of 0.55 (about the 90th percentile of unrelated pairs). Exact time overlap, shared beats and same-scene overlap are added as direct duplicate signals.

## 7. The reviewer's preferred promo was held back by the diversity pass
- **What it produced.** In blind pair P1, I preferred the event-ranking baseline's #1 (E3: guards drag Juliana and the scarred stranger intervenes) over PROMOIQ's #1 (E27 accusation scene). PROMOIQ had found the E3 scene too: it is validated at **7.83**, above five shortlisted promos. MMR held it back because its text is 0.69 similar to the E24 riding-crop promo (both show the heroine being manhandled).
- **Decision.** I left the rule alone. Treating these as one angle is defensible, and tuning the system until it agrees with my own review would be overfitting.
- **Product fix.** `ranked_promos.json`, the editorial report and the app now list "strong alternatives held back for diversity", each with the pick that displaced it. The editor can swap one in.

## 8. Cold-viewer judge missed an off-screen referent (open)
- **What it produced.** The E36 promo ("They arrested her!" → "Arrested?") never shows or names the woman everyone is talking about. The offline judge still rated it standalone (clarity 7.6). I rated it 2/5.
- **Why it isn't fixed.** Checking pronoun referents needs coreference resolution. The LLM cold-viewer judge (`ColdViewerJudge._llm`) is built for exactly this. It is implemented but was not run: no API key was available.

## 9. The standalone-clarity proxy doesn't discriminate (open)
The benchmark shows a 100% standalone pass rate for PROMOIQ *and* both baselines. The offline clarity proxy starts at 6 and adds bonuses, so it rarely falls below the gate of 5. Two related weaknesses:
- The offline cold-viewer judge is not independent of the generator: it reuses the same clarity function.
- It produces shallow answers, such as "what is happening: Poison."

Both are reasons to run the LLM judges, ideally a different model from the generator (`PROMOIQ_JUDGE_PROVIDER`).

## 10. Episode order is not story order (open)
Several episodes retell or overlap others:
- The E1 altar scene is retold in E4.
- The ring ordeal appears in E10 and E13.
- The garden attack appears in E17 and E18.
- The wedding storming spans E42–E45.

The "later in the series" logic used for spoilers and information gaps relies on episode number. Retellings are caught as near-duplicates (semantic 0.745 for E1/E4), but chronology is not reconstructed.

## 11. The local app exposed the confidential pack on the network (fixed)
- **What it produced.** Streamlit's default startup printed a *Network URL* and an *External URL*. The app was listening on all interfaces, so anyone on the same network could have opened the screenplays.
- **How it was detected.** The startup log.
- **Fix.** `.streamlit/config.toml` now binds the app to `localhost` and turns off usage statistics.

---

## Where PROMOIQ picks badly today (for the editor)
- **Talky promos rank well.** Dialogue-heavy duels score high because the scripts annotate dialogue richly. PROMO-04 runs 51.6 s of mostly speech, which may be long for TikTok.
- **About 83% of the 2,124 rejections are weak cliffhangers** (1,765 candidates).
- **Most continuous windows end on a reaction or a resolution, not a question.** The remaining rejections are weak hooks (669), spoiler risk (92), not standalone (14) and incoherent joins (5); one candidate can fail several gates.
- **Screenplay text only.** The tool has no access to performance, music or the actual edit.

---

# Live Gemini run, free tier (added after the offline build)

These are failures from actually running the LLM path on the full pack with a free Gemini key.
Before this run, the LLM path had only been tested against a fake provider. Every number below
comes from `outputs/gemini/` (private) and `evaluation/results/compare_offline_vs_llm.json`.

| # | Failure | Found by | Status |
|---|---|---|---|
| 12 | Default model retired for new keys | Smoke test | Fixed |
| 13 | Valid answers rejected over JSON shape | Live run | Fixed |
| 14 | Per-minute limit misread as daily exhaustion | Live run | Fixed |
| 15 | "No quote given" counted as an invented quote | Quote audit | Fixed |
| 16 | LLM scores inflated and barely agree with the offline scorer | Measurement | Partly contained |
| 17 | LLM cold viewer's verdict contradicts its own score | Judge outputs | Open |
| 18 | Free-tier judge budget too small for a diverse shortlist | Live run | Partly fixed: 19 judged over two days |
| 19 | A wrong assumption: LLM-only extraction keeps real timestamps | Benchmark | Documented |

## 12. Default model retired for new keys
- **What happened.** `gemini-2.5-flash`, my default, returned **404**: "no longer available to new users". The client also retried this 404 three times, which can never help.
- **Fix.**
  - The default is now `gemini-3.8-flash`.
  - `python -m src.llm.smoke` lists the models a key can see before any real run.
  - 400/401/403/404 errors fail immediately.
- **In the run itself,** 3.8-flash and 3.7-flash were overloaded (**503**, five back-offs on a single batch). Annotation was switched to `gemini-3-flash-preview`. Batch 1 was redone on it so all 50 episodes are scored by one model; the 3.8-flash answer stays in the raw log.

## 13. Valid answers rejected over JSON shape
- **What happened.** Gemini's first episode answer failed validation with **92 errors**. It returned `viewer_learns` and `unknown` as *lists* of sentences instead of strings (46 beats × 2 fields). The content was fine.
- **Fix.** All LLM-facing schemas now inherit `LenientModel`. It converts list↔string and string numbers and booleans. Wrong *content* still fails.
- The cached answer was re-validated after the fix, so no request was wasted.

## 14. A per-minute limit misread as daily exhaustion
- **What happened.** The Baseline A pre-warm stopped after 12 episodes. It reported "daily quota exhausted" and skipped 38 calls.
- **Why it was wrong.** A probe minutes later showed `gemini-3.6-flash` still answering. My check matched the word "daily" anywhere in the error text, and my log cut the error off before Google's `quotaId`.
- **Fix.** Only Google's `quotaId` decides now: `PerMinute` means wait 61 s, `PerDay` means stop. The quota ID is logged.
- **The real limit,** read from a genuine per-day error: `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **20 requests per day per model**.

## 15. "No quote given" was flagged as an invented quote
- **What happened.** After batch 1, 54 of 205 beats were flagged `[UNVERIFIED QUOTE]`. All 54 simply had an empty quote field. Every quote Gemini actually gave matched the script.
- **Fix.** The two cases are now counted separately.

**Full-run quote audit** (gemini-3-flash-preview, 1,503 quotes given):

| Result | Quotes | Share |
|---|---|---|
| Verbatim or matching apart from punctuation | 1,433 | 95.3% |
| Stitched or reordered from the beat's own words | 62 | — |
| Partial match | 1 | — |
| Mostly absent from the cited beat (invented or wrong beat) | 7 | 0.5% |

All 70 quotes that aren't verbatim are flagged; none is silently trusted.

## 16. LLM scores are inflated and barely agree with the offline scorer
- **What happened.** On the 1,631 beats Gemini scored:

| Feature | Offline mean (0–5) | Gemini mean (0–5) | Rank correlation |
|---|---|---|---|
| Stakes | 0.41 | 4.22 | 0.19 |
| Curiosity | 0.54 | 3.37 | 0.09 |
| Emotion | 1.23 | 3.82 | 0.36 |
| Conflict | 1.12 | 3.07 | 0.46 (highest) |

  Category labels overlap by Jaccard 0.13. With scores this high, **2,342** candidates pass the gates, against 1,243 offline. So the gates filter less.
- **What still works.** The code validator rejected **152** candidates that the LLM rated as strong hooks but where nothing substantive happens in the first 3 seconds (`hook_evidence_first_3s`). This is the "AI interprets, code verifies" split doing its job.
- **Proposed fix (not run, needs more quota).** Calibrate LLM scores per show, using rank or percentile instead of the raw 0–5 values, before applying the gates. Also give the model scored reference examples in the prompt.

## 17. The LLM cold viewer's verdict contradicts its own score (open)
- **What happened.** The LLM cold viewer (`gemini-3.1-flash-lite`, which sees only the promo) said **standalone = false for 8 of 9** candidates in round 1, and **15 of 19** once round 2 was added. Yet its mean clarity score is 6.2/10, which passes the gate of 5. Only 3 of the 19 were rejected as not standalone.
- **Why.** Its "missing information" lists are almost all backstory, such as "why is Juliana in custody" or "the history between Juliana and Catherine". A promo is designed to withhold exactly this. The judge prompt mixes up "I can follow what is happening" with "I know the backstory".
- **What this confirms.** The offline clarity proxy, which passes 100% of promos (§9), is far too lenient.
- **Proposed fix.** Split the question into two: "Is the situation and conflict legible?" (a gate) and "What backstory is missing?" (information for the editor). Then gate on the first only.

## 18. The free-tier judge budget is too small for a diverse shortlist (partly fixed)
- **What happened in round 1.** With 20 requests per day, I could afford LLM judging for only **9** distinct candidates. Four failed the gates (3 not standalone, 1 spoiler), so the Gemini shortlist had only **5** promos.
  - All 5 came from the episode 28–32 dungeon/poison arc.
  - 2 of the 5 were same-scene near-duplicates (near-duplicate rate 20%, against 0% offline).
  - None of them overlapped the offline shortlist.
- **Round 2.** After the daily reset, 10 more candidates were judged: 20 new calls, while the first 9 came from cache. A dry run with the live call stubbed out confirmed that count before any quota was spent.
  - 5 of the 19 failed the gates (3 not standalone, 2 spoiler risk), and the final-audit gate (§21) later rejected a sixth.
  - The shortlist is now **10** promos from **9 episodes** (E3, 12, 23, 27, 28, 29 ×2, 30, 31, 32).
  - One pick shares the offline #1's start (E27) and ends 3.9 s later.
  - So the single-arc result was mostly an artefact of the small judged pool. 7 of the 10 picks still come from episodes 27–32.
- **What is still open.** 19 candidates is below the 30+ I would judge on a paid tier (`PROMOIQ_JUDGE_BUDGET`). The cache makes the run resumable day by day.
- **Other costs.**
  - Judging took **13 min** for 20 calls, about 38 s per call including the 13 s pacing. The whole offline run takes about 20 s.
  - On `gemini-3.6-flash`, the daily cap arrived after 17 answered calls. Together with the three 503 errors that day, that makes 20. So failed 503 calls probably count against the free-tier quota, and retries are not free.

## 19. A wrong assumption: LLM-only extraction keeps real timestamps
- **What I assumed.** I expected Baseline A (one prompt per raw episode, no parsing help) to invent timestamps.
- **What happened.** `gemini-3.6-flash` returned promos for 28 episodes:
  - **100%** of its cut points are real screenplay timestamps, with 0 unparseable answers.
  - It cut through a line in 3 of 28 promos.
  - 6 of 28 fall outside 20–60 s, including **5 of its own top 10** (61–86 s). Round 1's 12 episodes had hidden this: 9 of 10 were in range.
- **What it means.** PROMOIQ's real advantages over a good LLM are guarantees and checks, not basic timestamp extraction:
  - Every cut is guaranteed legal, never just usually right.
  - It searches every window, plus multi-segment joins.
  - It applies gates and diversity, and explains its rejections.
- **Caveat.** Baseline A covers 28 of 50 episodes:
  - Round 1 lost 38 calls to failure 14 and then to the quota.
  - Round 2 added episodes 13–28.
  - Episode 29's answer failed validation, and the retry hit the daily cap.

# Final audit (independent re-check, added last)

Found by re-checking every recommended promo against the raw screenplay files with a separate parser
(`evaluation/verify_against_source.py`), a clean install from `requirements.txt`, and blind review round 2.
The mechanical checks all passed:
- 20 shortlisted promos and 7 alternatives, across both runs.
- Every cut is a real source timestamp, no cut splits a line, every duration is 20–60 s, and every evidence quote is in its cited block.

The failures below are about judgment and packaging.

| # | Failure | Found by | Status |
|---|---|---|---|
| 20 | Offline #1 ends one line before its payoff | A (blind review 2) | Open: scorer change not re-validated |
| 21 | A Gemini pick ends on a resolution; the judge said so and the gates ignored it | A (blind review 2) | Fixed: the judge's verdict is now a gate |
| 22 | Same payoff repeated across episodes slips past duplicate detection | A (blind review 2) | Open |
| 23 | Heuristic explanations quoted camera directions as stakes and invented "X vs Y" conflicts | A (reading outputs) | Fixed + test |
| 24 | `requirements.txt` named the legacy Gemini SDK | A (clean install) | Fixed |
| 25 | ZIP upload check used a string prefix | A (code read) | Fixed |

## 20. The offline #1 ends one line before its payoff (open)
- **What happened.** PROMO-01 (E27, 01:32.80–02:04.60) ends on the line "You hear that?!". The next line, 1.7 s later, is the twist: the villain turns Juliana's words into a confession. The blind reviewer dropped the clip. The Gemini run's version of the same scene ends 3.9 s later and includes the twist; the reviewer rated it cliffhanger 5/5.
- **Why.** The tool did test this ending: the "later ending" boundary counterfactual (02:08.50) is in `ranked_promos.json`. But the offline cliffhanger scorer gave the question mark 9.37 and the accusation 7.98, so it kept the shorter cut. The scorer rewards a literal question more than a reveal that creates one.
- **Proposed fix (not applied).** Score a reversal or accusation in the last line at least as high as a question, then re-run the blind review. I have not changed the scorer this late, because it would change the shortlist with no time to re-validate it.

## 21. A Gemini pick ended on a resolution, and the gates ignored the judge that said so (fixed)
- **What happened.** Gemini PROMO-10 (E2) ended on Juliana's defiant exit line while the score swells. The scene's conflict is resolved and nothing is left open.
- **Why.** Gemini's integrity judge had already said so: `ending_leaves_question = false` and `answered_inside_promo = true`. But the gates read only the generator's cliffhanger score (9.39, inflated, §16), and "answered inside" cost only 0.4 points. So a judge verdict that the promo breaks the brief's cliffhanger rule was computed and then outweighed. That contradicts the rule that hard failures can't be bought back with high scores.
- **Fix.**
  - The judge saying the ending leaves no open question is now a gate.
  - "Answered inside" stays a soft penalty, because the heuristic judge flags it on 482 offline candidates and is too noisy to gate on.
- **Effect.**
  - Offline: no candidate affected, and the shortlist is byte-identical.
  - Gemini, re-run entirely from cache with 0 new calls: exactly this candidate is rejected. An E29 clip (00:56.60–01:28.80) enters at #10, which puts two E29 clips from the same scene on the list (near-duplicates 10%).
  - A test checks that the judge's verdict rejects candidates. It fails on the old code.

## 22. The same payoff repeated across episodes slips past duplicate detection (open)
- **What happened.** Both shortlists report 0% near-duplicates, yet:
  - Gemini PROMO-04 and PROMO-08 both end on the "call it witchcraft" threat (E30, E29).
  - Gemini PROMO-03 and PROMO-05 share the dungeon iron threat (E32, E31).
  - Offline PROMO-04 and PROMO-05 turn on the same "beauty, money, family" taunt (E32, E31).
- **Why.** Duplicate detection compares time overlap, shared beats, the same scene and whole-promo semantic similarity. It does not compare the *endings* of promos from different episodes, where a series repeats its own beats.
- **Proposed fix.** Add an ending-similarity term: embed the last line or beat of each promo and penalise shared payoffs in the diversity pass.

## 23. Heuristic explanations quoted camera directions and invented conflicts (fixed)
- **What happened.** In the offline `ranked_promos.json`:
  - The heuristic cold viewer's `why_it_matters` was a camera direction for 5 of 10 promos (for example "Low-angle shot of Catherine…").
  - Its `central_conflict` paired any two speakers, such as "Prudence vs Juliana" for a maid warning her mistress.
- **Fix.**
  - Stakes now come only from spoken lines.
  - "What is happening" ignores camera directions.
  - A conflict is named only if it is a known antagonistic pair (hostility > warmth) and both characters speak in the promo; otherwise "not explicit on screen".
  - These are display fields, so no score changed. The re-run's shortlist, alternatives, candidates and gates are byte-identical apart from those fields.
  - A test now forces the worst case (camera lines carrying the top stakes signal). It fails on the old code and passes on the new.

## 24. `requirements.txt` named the legacy Gemini SDK (fixed)
- **What happened.** The live run used `google-genai`, but `requirements.txt` listed only `google-generativeai`. A clean install would have run the Gemini path on the untested legacy fallback.
- **Fix.** The requirement is now `google-genai>=1.0`. A fresh venv install, the test suite and the offline pipeline were all re-run from a clean copy of the tracked files.

## 25. The ZIP upload check used a string prefix (fixed)
- **What happened.** The app rejected ZIP entries outside the upload folder with `str(target).startswith(folder)`. An entry like `../uploaded_show_evil/x.txt` passed that check.
- **Fix.** It now uses `Path.is_relative_to`.

