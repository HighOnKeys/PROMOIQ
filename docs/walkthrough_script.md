# PROMOIQ walkthrough: 3-minute recording script

Record your screen and narrate in your own voice. Run everything live. Nothing here needs faking, because the full pack runs in about 20 seconds.

**Before you start:**
- Have a terminal open in the project folder.
- Start the app (`streamlit run app.py`). It opens on `localhost:8501`.
- Close anything that shows the raw scripts outside the app.

| Time | On screen | What to say |
|---|---|---|
| 0:00–0:25 | README title, then the brief's promo definition | "Today an editor watches every episode to find promos, and Kuku ships new shows every week. PROMOIQ reads the screenplays and returns a ranked shortlist of 20–60 second promos: exact cut points, the hook, the cliffhanger and why each one works. I treat a promo as a sequence to construct, not a scene to score." |
| 0:25–0:55 | Terminal: `python -m src.pipeline`, then the app's **Overview** tab (characters, conflicts, tension curve for episode 1) | "It parses all 50 episodes, 3,727 timestamped events, into 1,633 cut units. A cut is only allowed where no line of dialogue gets sliced. It builds a story bible and finds the real rivalries. Allies don't count: an early version thought the husband and wife were enemies. And it draws a tension curve for each episode." |
| 0:55–1:45 | Press **Run analysis** in the app, watch the progress bar, then open **Ranked promos** | "Every legal 20–60 second window is enumerated exactly. Multi-segment promos come from a beam search that joins only moments sharing a rival or a conflict. Each candidate is scored on hook, cliffhanger, clarity, emotion, open question and coherence, then checked: real timestamps, duration, and no mid-line cuts. 3,367 candidates, 1,243 survive, and a diversity pass picks ten promos across six angles. All of this runs offline at zero cost." |
| 1:45–2:20 | **Promo inspector**: PROMO-01, then PROMO-09 (multi-segment). Show segments, the script view, evidence rows and hard checks | "Here's #1: 32 seconds of episode 27, opening on an accusation. It cuts on the villain's 'You hear that?!'. My audit's blind reviewer said it should run 3.9 seconds longer, to include her twist. The tool tested that longer cut, it's here in the boundary counterfactuals, but my cliffhanger scorer preferred the question mark. That's a known failure, and I left it rather than retune at the last minute. Every claim links to an event ID and a source line. PROMO-09 joins episodes 42 and 44 into one storyline." |
| 2:20–2:45 | **Evaluation** tab (benchmark table), then `evaluation/failure_analysis.md` | "Against two baselines, keyword windows cut mid-line 90% of the time. Event ranking hooks well but ends on a cliffhanger only 40% of the time. In blind review, PROMOIQ's hooks were weaker than the baseline's, so I fixed the scorer, and I say so. A second blind review in my final audit found openings are still the weak spot. Both reviews were done by an AI, not a human. The failure log lists 25 real failures and what I did about each." |
| 2:45–3:00 | README sections "Swapping the model" and "Confidentiality" | "It works on a new show with no code changes, and any LLM plugs in with one environment variable, cached and logged. I ran it live on Gemini's free tier for zero dollars. Limits: text only, no video or audience data, and the editor verifies every cut. It stays local because the scripts are confidential." |

**Tips**
- Before recording, check that the numbers you say match `outputs/final/run_metadata.json`. They change if you change the weights.
- Don't scroll through raw episode files. Show the app and the outputs instead.
