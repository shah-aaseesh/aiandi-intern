"""Classify forum comments as acceptable, toxic or spam with Cloudflare's Clef model.

Usage:
    python classify.py                 # classify comments.json with clef, write results.json + results.html
    python classify.py --v2            # classify comments-v2.json, write results-v2.json + results-v2.html
    python classify.py --model clef-flash
    python classify.py --html-only     # rebuild the HTML from the saved JSON, no API calls
"""

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
CREDS_FILE = HERE / "cloud-flare.local.sh"

LABELS = ["acceptable", "toxic", "spam"]

QUESTION = {
    "type": "choice",
    "instructions": (
        "`comment` is a post on the discussion forum of a college course where students ask "
        "questions and share their work on assignments about building AI artifacts. "
        "`author` is the poster's display name and `thread` is the forum thread it was posted in. "
        "Decide how a forum moderator should label `comment`."
    ),
    "criteria": {
        "acceptable": (
            "A genuine post from someone taking part in the course: questions, answers, sharing "
            "their own work or a useful resource, feedback, jokes, casual language, or complaints "
            "and criticism of the course, a tool or someone's work that do not insult anyone. "
            "Blunt or frustrated posts are still acceptable if they attack no one."
        ),
        "toxic": (
            "Insults, harasses, threatens, mocks, demeans or shames a person or group (another "
            "student, the instructor, or people in general), uses slurs or hateful language, or is "
            "sexually explicit. Swearing aimed at a person counts as toxic."
        ),
        "spam": (
            "Unsolicited advertising or promotion that is not about learning in the course: selling "
            "assignment-writing or exam help, paid courses, products, followers, crypto or "
            "money-making schemes, or contact numbers and links to buy something."
        ),
    },
}

# Criteria for the harder comments-v2.json set: spell out the cases where the surface of a
# comment points the wrong way (quoted insults, blunt feedback, genuine links and numbers).
QUESTION_V2 = {
    "type": "choice",
    "instructions": (
        "`comment` is a post on the discussion forum of a college course where students ask "
        "questions and share their work on assignments about using and evaluating AI tools. "
        "`author` is the poster's display name and `thread` is the forum thread it was posted in. "
        "Decide how a forum moderator should label `comment`. Judge what the poster is doing, "
        "not just the words used: a post can contain rude words without being rude, and a polite "
        "post can still be an advert."
    ),
    "criteria": {
        "acceptable": (
            "A genuine post from someone taking part in the course: questions, answers, replies to "
            "other students, sharing their own work or a useful free resource, feedback, jokes, "
            "casual language, or complaints about grades, the course or a tool. Blunt or harsh "
            "criticism of someone's work is acceptable if it criticises the work, not the person. "
            "Quoting or reporting offensive text in order to discuss it (for example, prompts used "
            "to test a chatbot, or what a chatbot said) is acceptable. Mentioning a link, phone "
            "number or product is acceptable when it helps with the course and sells nothing."
        ),
        "toxic": (
            "The poster themselves insults, harasses, threatens, mocks, demeans or shames a person "
            "or group (another student, the instructor, or people in general), uses slurs or "
            "hateful language, or is sexually explicit. Swearing aimed at a person counts as toxic. "
            "Insults disguised as jokes, sarcasm or 'just being honest' still count."
        ),
        "spam": (
            "Unsolicited advertising or promotion that does not help with learning in the course: "
            "selling or offering assignment-writing, leaked exam papers or answers, paid courses, "
            "products, followers, crypto or money-making schemes, or contact numbers and links to buy "
            "something or to join such a service. It counts even when it is phrased as a friendly "
            "tip or a reply to a question."
        ),
    },
}

SETS = {
    "v1": {"comments": "comments.json", "json": "results.json", "html": "results.html", "question": QUESTION},
    "v2": {"comments": "comments-v2.json", "json": "results-v2.json", "html": "results-v2.html",
           "question": QUESTION_V2},
}


def load_creds():
    if not CREDS_FILE.exists():
        raise SystemExit(f"Missing {CREDS_FILE.name}: copy cloud-flare.sh to it and fill in your account ID and token.")
    creds = dict(re.findall(r'export (\w+)="([^"]*)"', CREDS_FILE.read_text()))
    return creds["CLOUDFLARE_ACCOUNT_ID"], creds["CLOUDFLARE_API_TOKEN"]


def classify(comment, account, token, model, question, retries=5):
    payload = {
        "state": {
            "comment": comment["comment"],
            "author": comment["author"],
            "thread": comment["thread"],
        },
        "questions": {"label": question},
    }
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/{model}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.load(response)
            if not data.get("success"):
                raise RuntimeError(f"comment {comment['id']}: {data.get('errors')}")
            answer = data["result"]["answers"]["label"]
            return {**comment, "label": answer["choice"],
                    "confidence": answer["confidence"], "probabilities": answer["probabilities"]}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"HTTP {e.code}: check the account ID and token in {CREDS_FILE.name}.")
            if e.code != 429 and e.code < 500 or attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def run(model, s):
    account, token = load_creds()
    comments = json.loads((HERE / s["comments"]).read_text(encoding="utf-8"))["comments"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: classify(c, account, token, model, s["question"]), comments))
    (HERE / s["json"]).write_text(json.dumps({"model": model, "results": results}, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"Classified {len(results)} comments with {model} -> {s['json']}")


def write_html(s):
    data = json.loads((HERE / s["json"]).read_text(encoding="utf-8"))
    results = data["results"]
    counts = {label: sum(r["label"] == label for r in results) for label in LABELS}
    low = sum(r["confidence"] < 0.5 for r in results)

    rows = []
    for r in results:
        probs = " ".join(
            f'<span class="p {k}" title="{k}">{k[0].upper()} {r["probabilities"].get(k, 0):.2f}</span>'
            for k in LABELS)
        unsure = " unsure" if r["confidence"] < 0.5 else ""
        rows.append(
            f'<tr class="row {r["label"]}{unsure}">'
            f'<td class="id">{r["id"]}</td>'
            f'<td><span class="badge {r["label"]}">{r["label"]}</span></td>'
            f'<td class="conf">{r["confidence"]:.2f}</td>'
            f'<td><div class="meta">{html.escape(r["author"])} · {html.escape(r["thread"])}</div>'
            f'{html.escape(r["comment"])}</td>'
            f'<td class="probs">{probs}</td></tr>')

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clef moderation results</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 24px; background: #f6f6f4; color: #222; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  .sub {{ color: #666; margin-bottom: 16px; }}
  .filters button {{ font: inherit; border: 1px solid #ccc; background: #fff; border-radius: 16px;
                     padding: 4px 12px; margin: 0 6px 6px 0; cursor: pointer; }}
  .filters button.on {{ background: #222; color: #fff; border-color: #222; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; margin-top: 12px; }}
  th, td {{ text-align: left; vertical-align: top; padding: 10px; border-bottom: 1px solid #eee; }}
  th {{ position: sticky; top: 0; background: #fff; font-size: 13px; color: #666; }}
  .id, .conf {{ font-variant-numeric: tabular-nums; color: #666; white-space: nowrap; }}
  .meta {{ font-size: 12px; color: #888; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-weight: 600; font-size: 13px; }}
  .badge.acceptable {{ background: #e3f4e6; color: #1d6b2c; }}
  .badge.toxic {{ background: #fde3e1; color: #a1251b; }}
  .badge.spam {{ background: #fff0cc; color: #8a5a00; }}
  tr.toxic {{ border-left: 4px solid #d9443a; }}
  tr.spam {{ border-left: 4px solid #e0a100; }}
  tr.unsure .conf {{ color: #a1251b; font-weight: 700; }}
  .probs {{ white-space: nowrap; font-size: 12px; font-variant-numeric: tabular-nums; }}
  .p {{ display: block; color: #666; }}
  @media (max-width: 700px) {{ body {{ padding: 12px; }} .probs {{ display: none; }} }}
</style></head><body>
<h1>Clef moderation results</h1>
<div class="sub"><code>{s["comments"]}</code> · {len(results)} comments · model <code>{data["model"]}</code> ·
  {counts["acceptable"]} acceptable · {counts["toxic"]} toxic · {counts["spam"]} spam ·
  {low} with confidence below 0.5 (shown in red)</div>
<div class="filters">
  <button class="on" data-f="all">All</button>
  <button data-f="acceptable">Acceptable</button>
  <button data-f="toxic">Toxic</button>
  <button data-f="spam">Spam</button>
  <button data-f="unsure">Low confidence</button>
</div>
<table><thead><tr><th>#</th><th>Clef label</th><th>Conf.</th><th>Comment</th><th>Probabilities</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table>
<script>
  document.querySelectorAll(".filters button").forEach(b => b.onclick = () => {{
    document.querySelectorAll(".filters button").forEach(x => x.classList.toggle("on", x === b));
    document.querySelectorAll("tr.row").forEach(r =>
      r.style.display = b.dataset.f === "all" || r.classList.contains(b.dataset.f) ? "" : "none");
  }});
</script>
</body></html>
"""
    (HERE / s["html"]).write_text(page, encoding="utf-8")
    print(f"Wrote {s['html']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="clef", choices=["clef", "clef-flash"])
    parser.add_argument("--v2", action="store_true", help="use comments-v2.json and write the -v2 results")
    parser.add_argument("--html-only", action="store_true")
    args = parser.parse_args()
    s = SETS["v2" if args.v2 else "v1"]
    if not args.html_only:
        run(args.model, s)
    write_html(s)
