"""
run_extraction.py — run the eval arms over the labelled sample.

Three prompt arms, chosen because the smoke test showed exactly what breaks:

    baseline    the original prompt
    units       adds one line about dual-unit energy labels, because the model
                read "180kJ / 42kcal" and returned 180 kcal — a 4.3x error on
                the most important field, and a systematic one on every EU panel
    strict      units + hard suppression of the reasoning preamble, which was
                what caused the truncation

Running all three over the same images answers a specific question: how much of
the error is capability and how much is instruction. That is a more useful
result than a single accuracy number.

Design notes
------------
Latency, not the rate limit, is the constraint: ~80s a call against a 40 RPM
allowance means you need roughly 30 in flight to approach the ceiling. The
limiter is set below the allowance deliberately — being throttled costs more
time than the headroom does.

Everything is resumable. Results append to JSONL as they complete, and a rerun
skips work already done, so an interrupted run is not a lost run.

Usage
-----
    python3 run_extraction.py --arm baseline --limit 20      # start small
    python3 run_extraction.py --arm baseline
    python3 run_extraction.py --arm units
    python3 run_extraction.py --arm strict
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE_URL = "https://integrate.api.nvidia.com/v1"
UA = "NutritionEval/0.1 (pmadarkar@gmail.com)"

FIELDS = ["energy_kcal", "fat_g", "saturated_fat_g", "carbohydrates_g",
          "sugars_g", "protein_g", "salt_g"]

_BASE = """You are reading a nutrition information panel from a food package.

Return a JSON object with these exact keys, giving values PER 100g (or per 100ml
for liquids):

  energy_kcal, fat_g, saturated_fat_g, carbohydrates_g, sugars_g,
  protein_g, salt_g

Rules:
- Numbers only, no units, no ranges.
- If the panel is per serving rather than per 100g, convert using the stated
  serving size.
- If a value is not present or not legible, use null.
- Do not guess. null is a valid and useful answer."""

_UNITS = """

IMPORTANT — energy is the field most often got wrong:
- European panels show energy twice, as kJ AND as kcal, e.g. "180kJ / 42kcal".
  Return the kcal figure (42), never the kJ figure (180).
- kJ is always the larger number. If your energy value looks about 4x larger
  than expected, you have taken the kJ figure by mistake.
- If ONLY kJ is shown, divide by 4.184 to get kcal.
- US panels show "Calories" — that is already kcal."""

_STRICT = """

Output format: return the JSON object and nothing else. No explanation, no
reasoning, no steps, no markdown fences. Your entire response must start with
{ and end with }."""

_COT = """

Work through the panel first — identify the column you are reading, note the
serving basis, and do any conversion out loud. Take as long as you need.

Then, as the very last thing in your response, output the JSON object on its
own. Nothing after it. Everything before it is working, and will be ignored."""

PROMPTS = {
    "baseline": _BASE,
    "units": _BASE + _UNITS,
    "strict": _BASE + _UNITS + _STRICT,
    # The arm the other three imply: strict proved that suppressing reasoning
    # costs accuracy on panels needing arithmetic, while baseline proved that
    # unconstrained output does not parse. This constrains where the answer
    # goes without removing the working space that produces it.
    "cot": _BASE + _UNITS + _COT,
}


class RateLimiter:
    """Token bucket. Set below the published allowance on purpose — a 429
    costs more time than the unused headroom."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
                self.next_at += self.interval
            else:
                self.next_at = now + self.interval


def load_sample(sample: Path, labels: Path | None) -> list[dict]:
    rows = [json.loads(l) for l in sample.open(encoding="utf-8")]
    if not labels:
        return rows
    lab = {r["code"]: r.get("label") for r in json.loads(labels.read_text())}
    keep = [r for r in rows if lab.get(r["code"]) in ("panel", "partial")]
    dropped = len(rows) - len(keep)
    print(f"  {len(keep)} usable images ({dropped} dropped as not-panel/unreadable)",
          file=sys.stderr)
    for r in keep:
        r["panel_label"] = lab[r["code"]]
    return keep


def fetch_image(url: str, size: str) -> str | None:
    if size != "400":
        url = url.replace(".400.jpg", f".{size}.jpg")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
        r.raise_for_status()
    except Exception:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(r.content).decode()


def parse_json(raw: str) -> dict | None:
    """Recover the answer object from a response that may contain prose.

    Tries the LAST balanced object first, because a model that reasons before
    answering puts the answer last — and reasoning text can itself contain
    braces, which the outermost-braces approach would swallow. Falls back to
    outermost for responses that are a single object wrapped in prose.
    """
    if not raw or "{" not in raw:
        return None

    starts = [i for i, ch in enumerate(raw) if ch == "{"]
    for start in reversed(starts):
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start:i + 1])
                    except (ValueError, json.JSONDecodeError):
                        break
                    if isinstance(obj, dict) and any(f in obj for f in FIELDS):
                        return obj
                    break

    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None


def one(client, rec: dict, prompt: str, model: str, size: str, limiter: RateLimiter) -> dict:
    out = {"code": rec["code"], "lang": rec.get("lang"),
           "panel_label": rec.get("panel_label"),
           "countries_tags": rec.get("countries_tags"),
           "serving_size": rec.get("serving_size"),
           "truth": rec["truth"]}

    data_url = fetch_image(rec["image_nutrition_url"], size)
    if not data_url:
        out["error"] = "image fetch failed"
        return out

    for attempt in range(3):
        limiter.wait()
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}],
                temperature=0.0,
                max_tokens=1400,   # the smoke test truncated at 400
            )
        except Exception as e:
            if attempt == 2:
                out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
                return out
            time.sleep(4 * (attempt + 1))
            continue

        raw = resp.choices[0].message.content or ""
        u = getattr(resp, "usage", None)
        out.update({
            "raw": raw,
            "seconds": round(time.time() - t0, 1),
            "tokens_in": getattr(u, "prompt_tokens", None),
            "tokens_out": getattr(u, "completion_tokens", None),
            "parsed": parse_json(raw),
        })
        return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=list(PROMPTS), required=True)
    ap.add_argument("--sample", type=Path, default=Path("sample.jsonl"))
    ap.add_argument("--labels", type=Path, default=Path("panel_labels.json"))
    ap.add_argument("--outdir", type=Path, default=Path("results"))
    ap.add_argument("--model", default=os.environ.get(
        "NVIDIA_MODEL_ID", "meta/llama-3.2-11b-vision-instruct"))
    ap.add_argument("--size", default="400", choices=["200", "400", "full"],
                    help="image resolution — an ablation in its own right")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--rpm", type=int, default=30, help="stay under the 40 allowance")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not os.environ.get("NVIDIA_API_KEY"):
        sys.exit("set NVIDIA_API_KEY")
    from openai import OpenAI

    rows = load_sample(args.sample, args.labels if args.labels.exists() else None)

    args.outdir.mkdir(exist_ok=True)
    # The model has to be in the filename. Without it a run against a different
    # model writes to the same file, sees every code already present, and exits
    # having done nothing — silently, which is the worst way to fail.
    short = args.model.split("/")[-1].replace("-vision-instruct", "").replace("llama-", "")
    parts = [args.arm]
    if short not in ("3.2-11b",):          # the default model stays unsuffixed
        parts.append(short)
    if args.size != "400":
        parts.append(args.size)
    out_path = args.outdir / ("-".join(parts) + ".jsonl")
    print(f"  writing to {out_path}", file=sys.stderr)

    done: set[str] = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["code"])
                except Exception:
                    pass
        print(f"  resuming — {len(done)} already done", file=sys.stderr)

    todo = [r for r in rows if r["code"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("nothing to do", file=sys.stderr)
        return 0

    est = len(todo) * 80 / max(1, args.workers) / 60
    print(f"\narm={args.arm} model={args.model} size={args.size}", file=sys.stderr)
    print(f"{len(todo)} calls · {args.workers} workers · {args.rpm} rpm cap", file=sys.stderr)
    print(f"rough estimate: {est:.0f} min\n", file=sys.stderr)

    client = OpenAI(base_url=BASE_URL, api_key=os.environ["NVIDIA_API_KEY"],
                    timeout=120.0, max_retries=0)
    limiter = RateLimiter(args.rpm)
    write_lock = threading.Lock()
    stats = Counter()
    t_start = time.time()

    with out_path.open("a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(one, client, r, PROMPTS[args.arm], args.model,
                            args.size, limiter): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            stats["error" if res.get("error") else
                  "parsed" if res.get("parsed") else "unparsed"] += 1
            stats["tokens_in"] += res.get("tokens_in") or 0
            stats["tokens_out"] += res.get("tokens_out") or 0
            with write_lock:
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                fh.flush()
            if i % 10 == 0 or i == len(todo):
                el = (time.time() - t_start) / 60
                print(f"  {i}/{len(todo)} · {el:.1f} min · "
                      f"parsed {stats['parsed']} unparsed {stats['unparsed']} "
                      f"err {stats['error']}", file=sys.stderr)

    print(f"\ndone in {(time.time()-t_start)/60:.1f} min → {out_path}", file=sys.stderr)
    print(f"  parsed   {stats['parsed']}", file=sys.stderr)
    print(f"  unparsed {stats['unparsed']}", file=sys.stderr)
    print(f"  errors   {stats['error']}", file=sys.stderr)
    print(f"  tokens   {stats['tokens_in']:,} in · {stats['tokens_out']:,} out",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
