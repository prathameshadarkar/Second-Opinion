"""
smoke_test.py — one product, one API call, before you build anything.

Answers the only question that matters right now: can this model read a
nutrition panel at all? If it can't, you've learned that for the cost of one
call instead of a weekend.

The test product is Nutella (barcode 3017620422003) because it has a clean
nutrition image and values that are easy to eyeball. Its Open Food Facts
record says, per 100g:

    energy 539 kcal · fat 30.9g · saturated fat 10.6g
    carbs 57.5g · sugars 56.3g · protein 6.3g · salt 0.107g

Setup
-----
    pip install openai requests
    export NVIDIA_API_KEY="nvapi-..."

Then set MODEL_ID below from the model card on build.nvidia.com — the exact
string is on the page for whichever vision model you picked.

    python smoke_test.py
    python smoke_test.py --barcode 5449000000996   # try another product
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

import requests

# --------------------------------------------------------------------------
# Paste the model ID from its page on build.nvidia.com. Every vision model
# lists it in the code sample on the right-hand side.
# --------------------------------------------------------------------------
MODEL_ID = os.environ.get("NVIDIA_MODEL_ID", "PASTE_MODEL_ID_HERE")

BASE_URL = "https://integrate.api.nvidia.com/v1"
UA = "NutritionEval/0.1 (pmadarkar@gmail.com)"

# The seven fields to extract. Keys are what we ask the model for; values are
# the matching Open Food Facts field, all per 100g.
FIELDS = {
    "energy_kcal": "energy-kcal_100g",
    "fat_g": "fat_100g",
    "saturated_fat_g": "saturated-fat_100g",
    "carbohydrates_g": "carbohydrates_100g",
    "sugars_g": "sugars_100g",
    "protein_g": "proteins_100g",
    "salt_g": "salt_100g",
}

PROMPT = """You are reading a nutrition information panel from a food package.

Return ONLY a JSON object with these exact keys, giving values PER 100g:

  energy_kcal, fat_g, saturated_fat_g, carbohydrates_g, sugars_g,
  protein_g, salt_g

Rules:
- Numbers only, no units, no ranges.
- If the panel gives energy only in kJ, convert: kcal = kJ / 4.184
- If the panel is per serving rather than per 100g, convert using the stated
  serving size.
- If a value is not present or not legible, use null.
- Do not guess. null is a valid and useful answer.

Return the JSON object and nothing else."""


def fetch_product(barcode: str) -> dict:
    """Pull one product from Open Food Facts."""
    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    params = {"fields": "code,product_name,image_nutrition_url,nutriments,nutrition_data_per"}
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 1:
        sys.exit(f"product {barcode} not found in Open Food Facts")
    p = data["product"]
    if not p.get("image_nutrition_url"):
        sys.exit(f"{barcode} has no nutrition panel image — pick another product")
    return p


def as_data_url(image_url: str) -> str:
    """Download the panel image and base64 it for the request."""
    r = requests.get(image_url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    b64 = base64.b64encode(r.content).decode()
    kb = len(r.content) / 1024
    print(f"  image: {kb:.0f} KB")
    if kb > 180:
        print("  note: large images may need the URL form instead of base64 —")
        print("        check the model card for its size limit")
    return f"data:image/jpeg;base64,{b64}"


def extract(data_url: str) -> tuple[dict | None, str]:
    """One VLM call. Returns (parsed, raw)."""
    from openai import OpenAI

    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        sys.exit("set NVIDIA_API_KEY first")
    if MODEL_ID == "PASTE_MODEL_ID_HERE":
        sys.exit("set MODEL_ID (top of this file) from the model card on build.nvidia.com")

    import time

    # A timeout matters here: without one the client waits ten minutes on a
    # stalled endpoint, which in a notebook looks identical to a hung kernel.
    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=90.0, max_retries=1)

    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            temperature=0.0,
            max_tokens=400,
        )
    except Exception as e:
        print(f"  call failed after {time.time() - t0:.0f}s: {type(e).__name__}: {e}")
        return None, ""

    print(f"  responded in {time.time() - t0:.1f}s")
    usage = getattr(resp, "usage", None)
    if usage:
        print(f"  tokens: {usage.prompt_tokens} in, {usage.completion_tokens} out")
    raw = resp.choices[0].message.content or ""

    # Models often wrap JSON in prose or a fenced block. Take the outermost
    # braces rather than trusting the whole response to parse.
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[start:end]), raw
    except (ValueError, json.JSONDecodeError):
        return None, raw


def compare(got: dict, truth: dict) -> None:
    print(f"\n{'field':<20} {'model':>10} {'database':>10} {'diff':>10}   ")
    print("-" * 56)
    hits = 0
    for key, off_key in FIELDS.items():
        m = got.get(key)
        t = truth.get(off_key)
        if m is None or t is None:
            note = "missing"
            diff = "—"
        else:
            d = float(m) - float(t)
            # 2% tolerance, since panels round and databases don't always
            ok = abs(d) <= max(0.05, abs(float(t)) * 0.02)
            hits += ok
            note = "match" if ok else "MISMATCH"
            diff = f"{d:+.2f}"
        ms = "null" if m is None else f"{float(m):g}"
        ts = "—" if t is None else f"{float(t):g}"
        print(f"{key:<20} {ms:>10} {ts:>10} {diff:>10}   {note}")
    print("-" * 56)
    print(f"{hits}/{len(FIELDS)} fields within 2% tolerance")

    if hits >= 5:
        print("\nGood enough to build on. Move to the sampling script.")
    elif hits >= 3:
        print("\nPartly working. Before giving up on the model, try:")
        print("  · the full-resolution image (swap .400.jpg for .full.jpg)")
        print("  · a product whose panel is in English")
        print("  · restating the per-100g instruction more forcefully")
    else:
        print("\nWeak. Check the raw response above — a formatting failure and a")
        print("reading failure look identical in this table and are not the same")
        print("problem. If it read the numbers but broke the JSON, that's fixable.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--barcode", default="3017620422003", help="default: Nutella")
    ap.add_argument("--full-res", action="store_true",
                    help="use the full-resolution panel image")
    args = ap.parse_args()

    print(f"model: {MODEL_ID}")
    p = fetch_product(args.barcode)
    print(f"product: {p.get('product_name', '?')}  ({p['code']})")
    print(f"  values are per: {p.get('nutrition_data_per', '?')}")

    img = p["image_nutrition_url"]
    if args.full_res:
        img = img.replace(".400.jpg", ".full.jpg")
    print(f"  panel: {img}")

    data_url = as_data_url(img)
    print("\ncalling the model…")
    got, raw = extract(data_url)

    print("\n--- raw response ---")
    print(raw.strip()[:900])
    print("--- end ---")

    if got is None:
        print("\nCouldn't parse JSON out of that. The model may have read the")
        print("panel fine and just wrapped it in prose — read the raw output above.")
        print("If so, tighten the prompt rather than changing model.")
        return 1

    compare(got, p.get("nutriments", {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
