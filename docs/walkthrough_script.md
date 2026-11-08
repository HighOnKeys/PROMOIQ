# PROMOIQ walkthrough: 3-minute recording script

About 370 spoken words, which is roughly 2 min 45 s at a calm pace, leaving room for the 12 s run. Everything runs live; nothing is faked.

## Before you record (5 min)

1. In Terminal:
   ```bash
   cd ~/Desktop/PROMOIQ && source .venv/bin/activate && streamlit run app.py
   ```
   The app opens at `localhost:8501`. Make the browser window wide enough that the left sidebar shows.
2. Open a second browser tab at `github.com/HighOnKeys/PROMOIQ/blob/main/evaluation/evaluation_report.md`.
3. Open a second Terminal tab in the project folder. Run `source .venv/bin/activate`, then type this command but **don't press Enter yet**:
   ```bash
   python -m evaluation.verify_against_source --input data/input outputs/app_run/final/ranked_promos.json
   ```
4. Close anything that shows raw episode files, `.env` or Drive.
5. Do one silent rehearsal and time it.

The video shows screenplay lines inside the app. Upload it **only to your Drive folder**, never to a public site.

---

## 1. Intro (0:00–0:15) · app home screen

> Hi, I'm Kumar Manas. This is PROMOIQ, for Kuku's Problem 1, finding promos. Today an editor watches every episode to pick 20-to-60-second promos. PROMOIQ reads the screenplays and hands the editor a ranked shortlist with exact cut points.

## 2. Run it (0:15–0:45) · sidebar **Run analysis**, then the **Overview** tab

On screen: press **Run analysis** with "Local folder" and the "heuristic" provider. Talk while the progress bar runs (about 12 s). When it finishes, point at the metrics.

> I'm running it on all 50 episodes. It parses 3,727 timestamped events and only allows a cut where no line of dialogue gets sliced, so every cut is a real screenplay timestamp. It scores hooks and cliffhangers, works out who is against whom, then searches every possible window, including joins across episodes. Done: 3,367 candidates, 1,243 pass every check, in about 12 seconds, at zero cost.

## 3. The shortlist (0:45–1:05) · **Ranked promos** tab

On screen: scroll the table, then open one expander.

> Here's what to post first. For each promo: the episode, the start and end of every segment, the hook in the first three seconds, the cliffhanger, and why it works. A diversity pass keeps the ten from being cuts of one scene.

## 4. One promo, including its failure (1:05–1:45) · **Promo inspector** tab, PROMO-01

On screen: the segment table, then the script text, the source evidence and the hard checks. Finish on the **Boundary counterfactuals** row `later_ending 01:32.80–02:08.50`.

> Number one is 32 seconds of episode 27. This is exactly what the viewer hears, every claim traces back to a source line, and it passes every hard check. Now an honest failure. It ends on "You hear that?!", but a blind reviewer said it should run 3.9 seconds longer, to the villain's twist. The tool tested that longer cut, it's right here, but my scorer preferred the question mark. It's logged as a known failure.

## 5. Checking my own output (1:45–2:05) · second Terminal tab

On screen: press Enter. Let the `OK` lines scroll, ending on `0 promo(s) with problems`.

> To check my own output, a separate script re-reads the raw screenplays with its own parser and re-checks every promo: timestamps, durations, split lines and quoted evidence. Zero problems. Tampered promos get caught.

## 6. How I know it's good (2:05–2:40) · GitHub tab, `evaluation_report.md`

On screen: scroll from the metrics table to "Live LLM run" and then "Blind review, round 2".

> Against simple baselines, keyword windows cut mid-line 90% of the time, and event ranking ends on a cliffhanger only 40% of the time. I also ran it live on Gemini's free tier: 80 calls, zero dollars. Two blind reviews agree that openings are the weak spot, and both were done by an AI, not a human. The failure log lists 25 real failures and what I did about each.

## 7. Close (2:40–2:55) · scroll the README to "Swapping the model"

> A new show is one command, and any LLM plugs in through an environment variable. The limit: it reads text only, so an editor still checks every cut on video. The code is on GitHub, and the outputs stay private. Thanks for watching.

---

## After recording

- **Trim:** in QuickTime use **Edit → Trim** so the video is 3:00 or less.
- **Shrink if large:** use **File → Export As → 1080p**.
- **Upload:** rename it `PROMOIQ_walkthrough.mov` and upload it to the PROMOIQ Drive folder.
- **Check:** Drive needs a few minutes to process a video. Then check that it plays in a private window.

If you change the weights in the sidebar, the numbers change. Check them against the Overview tab before you say them.
