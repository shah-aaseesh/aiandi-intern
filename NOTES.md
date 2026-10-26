# Notes

## How to run

1. Copy `cloud-flare.sh` to `cloud-flare.local.sh` and fill in your Cloudflare account ID and a
   Workers AI API token (Read + Edit). This file is git-ignored.
2. Run the classifier (Python 3, standard library only):

   ```
   python classify.py                    # uses the larger `clef` model
   python classify.py --model clef-flash # faster, cheaper model
   python classify.py --html-only        # rebuild results.html from results.json without calling the API
   ```

3. Open `results.html` in a browser. Buttons at the top filter by label, and "Low confidence"
   shows the comments Clef was least sure about (confidence below 0.5, shown in red).

The program sends each comment to Clef together with its author name and forum thread, so
usernames like `AssignmentGuru_99` can count as a spam signal. Raw answers, including the
probability for each label, are saved in `results.json`.

## Tools and models used

- **Cloudflare Workers AI, `clef` model:** classified the comments.
- **Claude Code (Claude Opus 5.5):** wrote `classify.py` and
  the HTML page, ran the classification, and committed and pushed the code.
- **Me:** checked every label by hand and wrote `MISTAKES.md`.

## How I found the task

It took me about 23 minutes, and it was a fairly easy task. Clef was new to me. One thing I
learned is that low confidence does not always mean the label is wrong: Clef was unsure about
three comments that were clearly toxic, and it still labelled them correctly. Working with an
AI assistant (Claude Code) was good.
