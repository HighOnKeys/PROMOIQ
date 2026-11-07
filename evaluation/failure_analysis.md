# PROMOIQ failure analysis

Every failure below happened during this build on the real 50-episode pack. Snapshots of the
affected runs are kept privately in `outputs/history/run1..run5_reviewed/` (they contain
screenplay excerpts, so they are not in the repository). Quotes are kept short on purpose.

How failures were found:
- (M) **Manual inspection**: I read the full text of every shortlisted promo after each run.
- (B) **Blind review**: round 1, 18 clips and 6 pairs with system labels hidden.
- (T) **Tests and measurement.**

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
