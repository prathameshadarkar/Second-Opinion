"""
make_charts.py — figures for the case study, generated from the results.

Writes standalone SVGs designed to be INLINED into the page rather than loaded
with <img>. Inlining lets them inherit the site's CSS variables, so they follow
the light/dark toggle instead of being a light rectangle on a dark page.

Four figures, each carrying one finding:

  fig-agreement.svg    where four prompt variants agree, accuracy is 82%;
                       where they disagree, 28%. The headline.
  fig-arms.svg         parse rate against accuracy per arm — the trade-off
  fig-kj.svg           the kilojoule substitution, before and after
  fig-style.svg        per-100g vs per-serving panels, by arm — the mechanism

Usage
-----
    python3 make_charts.py --arms baseline units strict cot units-full
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

FIELDS = ["energy_kcal", "fat_g", "saturated_fat_g", "carbohydrates_g",
          "sugars_g", "protein_g", "salt_g"]
KJ = 4.184

CSS = """<style>
.bg{fill:none}
.t{fill:var(--cs-text,#0F172A);font:500 13px Inter,system-ui,sans-serif}
.tb{fill:var(--cs-text,#0F172A);font:700 22px Inter,system-ui,sans-serif;letter-spacing:-.02em}
.ts{fill:var(--cs-soft,#94A3B8);font:400 11.5px Inter,system-ui,sans-serif}
.tm{fill:var(--cs-soft,#94A3B8);font:500 10.5px 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.08em}
.ax{stroke:var(--cs-line,#E5E9F0);stroke-width:1}
.b1{fill:var(--cs-accent,#1D4ED8)}
.b2{fill:var(--cs-soft,#94A3B8);opacity:.55}
.b3{fill:#F87171}
</style>"""


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", ".")
        for j in ("g", "kcal", "kJ", "mg", "ml", "%", "<", "~", " "):
            s = s.replace(j, "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def close(a, b):
    return abs(a - b) <= max(0.1, abs(b) * 0.02)


def per_serving(r):
    return any(t in ("en:united-states", "en:canada")
               for t in (r.get("countries_tags") or []))


def svg(w, h, body, title, desc):
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-labelledby="t d">{CSS}'
            f'<title id="t">{title}</title><desc id="d">{desc}</desc>'
            f'<rect class="bg" width="{w}" height="{h}"/>{body}</svg>')


def hbars(rows, w=680, title="", note="", pad_l=190, colour=None):
    """rows: [(label, pct, sublabel)]"""
    bar_h, gap, top = 34, 14, 46 if title else 14
    h = top + len(rows) * (bar_h + gap) + 34
    inner = w - pad_l - 70
    out = []
    if title:
        out.append(f'<text class="tm" x="0" y="16">{title}</text>')
    for i, (lab, pct, sub) in enumerate(rows):
        y = top + i * (bar_h + gap)
        bw = max(2, inner * pct / 100)
        cls = colour(i) if colour else ("b1" if i == 0 else "b2")
        out.append(f'<text class="t" x="{pad_l-12}" y="{y+16}" text-anchor="end">{lab}</text>')
        if sub:
            out.append(f'<text class="ts" x="{pad_l-12}" y="{y+31}" text-anchor="end">{sub}</text>')
        out.append(f'<rect class="{cls}" x="{pad_l}" y="{y}" width="{bw:.1f}" '
                   f'height="{bar_h}" rx="3"/>')
        out.append(f'<text class="tb" x="{pad_l+bw+12:.1f}" y="{y+25}">{pct:.1f}%</text>')
    if note:
        out.append(f'<text class="ts" x="0" y="{h-10}">{note}</text>')
    return w, h, "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--outdir", type=Path, default=Path("figures"))
    a = ap.parse_args()
    a.outdir.mkdir(exist_ok=True)

    data, truth, loaded = defaultdict(dict), {}, []
    stats = {}
    for arm in a.arms:
        p = a.results / f"{arm}.jsonl"
        if not p.exists():
            print(f"  skipping {arm}")
            continue
        loaded.append(arm)
        rows = [json.loads(l) for l in p.open(encoding="utf-8")]
        hits = seen = kj = eseen = 0
        style = {"per-100g": [0, 0], "per-serving": [0, 0]}
        parsed = 0
        for r in rows:
            truth[r["code"]] = r["truth"]
            if not r.get("parsed"):
                continue
            parsed += 1
            data[r["code"]][arm] = r["parsed"]
            st = "per-serving" if per_serving(r) else "per-100g"
            for f in FIELDS:
                want, got = num(r["truth"].get(f)), num(r["parsed"].get(f))
                if want is None:
                    continue
                seen += 1
                style[st][1] += 1
                if got is None:
                    continue
                ok = close(got, want)
                hits += ok
                style[st][0] += ok
                if f == "energy_kcal":
                    eseen += 1
                    if not ok and want > 0 and abs(got/want - KJ)/KJ <= .03:
                        kj += 1
        stats[arm] = {"n": len(rows), "parsed": parsed, "hits": hits, "seen": seen,
                      "kj": kj, "eseen": eseen, "style": style}

    if not loaded:
        return 1

    # ---- fig 1: agreement -------------------------------------------------
    agree_h = agree_n = dis_h = dis_n = 0
    for code, per_arm in data.items():
        if len(per_arm) < 2:
            continue
        for f in FIELDS:
            want = num(truth[code].get(f))
            if want is None:
                continue
            vals = [num(v.get(f)) for v in per_arm.values()]
            vals = [v for v in vals if v is not None]
            if len(vals) < 2:
                continue
            med = statistics.median(vals)
            ok = close(med, want)
            if all(close(v, med) for v in vals):
                agree_n += 1; agree_h += ok
            else:
                dis_n += 1; dis_h += ok
    tot = agree_n + dis_n
    r1 = [("All variants agree", 100*agree_h/agree_n if agree_n else 0,
           f"{agree_n:,} fields · {100*agree_n/tot:.0f}% of total"),
          ("Variants disagree", 100*dis_h/dis_n if dis_n else 0,
           f"{dis_n:,} fields · {100*dis_n/tot:.0f}% of total")]
    w, h, b = hbars(r1, title="ACCURACY BY INTER-PROMPT AGREEMENT",
                    note="Agreement does not correct errors — it detects difficulty.",
                    colour=lambda i: "b1" if i == 0 else "b3")
    (a.outdir/"fig-agreement.svg").write_text(svg(w, h, b,
        "Accuracy by agreement",
        f"Where prompt variants agree, {100*agree_h/max(agree_n,1):.0f}% accurate; "
        f"where they disagree, {100*dis_h/max(dis_n,1):.0f}%."))

    # ---- fig 2: arms ------------------------------------------------------
    rows = [(arm, 100*s["hits"]/s["seen"] if s["seen"] else 0,
             f"parse rate {100*s['parsed']/s['n']:.0f}%") for arm, s in stats.items()]
    rows.sort(key=lambda r: -r[1])
    w, h, b = hbars(rows, title="FIELD ACCURACY BY PROMPT VARIANT",
                    note="Parse rate and accuracy move in opposite directions.")
    (a.outdir/"fig-arms.svg").write_text(svg(w, h, b, "Accuracy by arm",
        "Field-level accuracy for each prompt variant, with parse rate."))

    # ---- fig 3: kJ --------------------------------------------------------
    rows = [(arm, 100*s["kj"]/s["eseen"] if s["eseen"] else 0,
             f"{s['kj']} of {s['eseen']} energy fields") for arm, s in stats.items()]
    w, h, b = hbars(rows, title="KILOJOULE READ AS KILOCALORIE",
                    note="Detected by ratio: a value within 3% of 4.184x truth.",
                    colour=lambda i: "b3")
    (a.outdir/"fig-kj.svg").write_text(svg(w, h, b, "Kilojoule substitution",
        "Share of energy fields where the model returned the kJ figure."))

    # ---- fig 4: panel style ----------------------------------------------
    rows = []
    for arm, s in stats.items():
        for st in ("per-100g", "per-serving"):
            hit, seen = s["style"][st]
            rows.append((f"{arm} · {st}", 100*hit/seen if seen else 0, ""))
    w, h, b = hbars(rows, title="ACCURACY BY PANEL STYLE", pad_l=230,
                    note="US panels are per serving and require conversion.",
                    colour=lambda i: "b1" if i % 2 == 0 else "b2")
    (a.outdir/"fig-style.svg").write_text(svg(w, h, b, "Accuracy by panel style",
        "Per-100g versus per-serving panels, by prompt variant."))

    print(f"wrote 4 figures to {a.outdir}/")
    for f in sorted(a.outdir.glob("*.svg")):
        print(f"  {f.name}  {f.stat().st_size:,} bytes")
    print("\nInline these into the page rather than using <img>, so they")
    print("inherit the site's colours and follow the light/dark toggle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
