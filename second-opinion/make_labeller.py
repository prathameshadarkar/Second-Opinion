"""
make_labeller.py — build a browser tool for labelling the sample images.

Open Food Facts is crowdsourced, so the "nutrition image" slot does not
reliably hold a nutrition panel. Nutella's is a photo of the jar; Coca-Cola's
is a proper table. Nothing in the metadata distinguishes them.

Before you can evaluate extraction you have to know which images could be read
by anyone, and that needs eyes. This writes a self-contained HTML page that
loads the sample's images from the Open Food Facts CDN in a grid, lets you
label them with the keyboard, and exports a JSON file.

Four labels:

    panel       a readable nutrition table
    partial     a panel, but cut off, blurred, or angled enough to lose values
    not-panel   front of pack, ingredients list, or something else entirely
    unreadable  too dark or too low-resolution to judge

"partial" earns its place: those are the cases where the model and the database
will disagree and neither is wrong, and lumping them with "panel" would make
the extraction score look worse than the model deserves.

Usage
-----
    python make_labeller.py --sample sample.jsonl
    open label.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Label nutrition panels</title>
<style>
:root{--bg:#0B0F1A;--surface:#141A26;--ink:#E8EDF5;--ink2:#A3B0C2;--ink3:#6E7C90;
--line:#212A38;--accent:#60A5FA;--ok:#34D399;--warn:#FBBF24;--bad:#F87171;--dim:#818CF8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,Inter,sans-serif;padding:0 0 90px}
header{position:sticky;top:0;z-index:10;background:rgba(11,15,26,.94);backdrop-filter:blur(10px);
border-bottom:1px solid var(--line);padding:16px 26px;display:flex;gap:26px;align-items:center;flex-wrap:wrap}
h1{font-size:1.05rem;font-weight:700;letter-spacing:-.02em}
.counts{display:flex;gap:16px;font-size:.82rem;color:var(--ink2);flex-wrap:wrap}
.counts b{font-variant-numeric:tabular-nums}
.bar{flex:1;min-width:120px;height:6px;background:var(--line);border-radius:100px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .2s}
button{font:inherit;font-size:.84rem;font-weight:600;background:transparent;color:var(--ink);
border:1px solid var(--ink3);border-radius:100px;padding:8px 18px;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;padding:22px 26px}
.card{background:var(--surface);border:2px solid var(--line);border-radius:12px;overflow:hidden;
cursor:pointer;transition:border-color .12s}
.card.sel{outline:2px solid var(--accent);outline-offset:2px}
.card img{width:100%;height:200px;object-fit:contain;background:#000;display:block}
.meta{padding:9px 11px;font-size:.72rem;color:var(--ink3);display:flex;justify-content:space-between;gap:8px}
.meta span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{padding:2px 9px;border-radius:100px;font-weight:700;font-size:.68rem;letter-spacing:.04em}
.card[data-l="panel"]{border-color:var(--ok)} .card[data-l="panel"] .tag{background:var(--ok);color:#04231a}
.card[data-l="partial"]{border-color:var(--warn)} .card[data-l="partial"] .tag{background:var(--warn);color:#3a2a02}
.card[data-l="not-panel"]{border-color:var(--bad)} .card[data-l="not-panel"] .tag{background:var(--bad);color:#3d0d0d}
.card[data-l="unreadable"]{border-color:var(--dim)} .card[data-l="unreadable"] .tag{background:var(--dim);color:#12103a}
footer{position:fixed;bottom:0;left:0;right:0;background:rgba(11,15,26,.96);border-top:1px solid var(--line);
padding:12px 26px;font-size:.8rem;color:var(--ink3);display:flex;gap:22px;flex-wrap:wrap;align-items:center}
kbd{background:var(--line);border-radius:4px;padding:2px 7px;font:600 .74rem ui-monospace,monospace;color:var(--ink)}
</style></head><body>
<header>
  <h1>Label nutrition panels</h1>
  <div class="bar"><i id="bar"></i></div>
  <div class="counts">
    <span><b id="c-done">0</b>/<b id="c-all">0</b> done</span>
    <span style="color:var(--ok)">panel <b id="c-panel">0</b></span>
    <span style="color:var(--warn)">partial <b id="c-partial">0</b></span>
    <span style="color:var(--bad)">not-panel <b id="c-not-panel">0</b></span>
    <span style="color:var(--dim)">unreadable <b id="c-unreadable">0</b></span>
  </div>
  <button id="save">Download labels</button>
</header>
<div class="grid" id="grid"></div>
<footer>
  <span><kbd>1</kbd> panel</span><span><kbd>2</kbd> partial</span>
  <span><kbd>3</kbd> not-panel</span><span><kbd>4</kbd> unreadable</span>
  <span><kbd>0</kbd> clear</span><span><kbd>&larr;</kbd><kbd>&rarr;</kbd> move</span>
  <span style="margin-left:auto">Labels are kept in this browser until you download them.</span>
</footer>
<script>
const DATA = __DATA__;
const KEYS = {1:"panel",2:"partial",3:"not-panel",4:"unreadable",0:null};
const STORE = "panel_labels_v1";
let labels = {}, cur = 0;
try { labels = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch(e) {}

const grid = document.getElementById("grid");
DATA.forEach((d,i) => {
  const c = document.createElement("div");
  c.className = "card"; c.dataset.i = i;
  if (labels[d.code]) c.dataset.l = labels[d.code];
  c.innerHTML = `<img loading="lazy" src="${d.img}" alt="">
    <div class="meta"><span>${d.name || d.code}</span>
    <span class="tag">${labels[d.code] || d.lang || ""}</span></div>`;
  c.onclick = () => { cur = i; paint(); };
  grid.appendChild(c);
});

function paint(){
  const cards = grid.children;
  let done = 0; const tally = {panel:0,partial:0,"not-panel":0,unreadable:0};
  for (let i=0;i<cards.length;i++){
    const code = DATA[i].code, l = labels[code];
    cards[i].classList.toggle("sel", i===cur);
    if (l){ cards[i].dataset.l = l; cards[i].querySelector(".tag").textContent = l; done++; tally[l]++; }
    else { delete cards[i].dataset.l; cards[i].querySelector(".tag").textContent = DATA[i].lang || ""; }
  }
  document.getElementById("c-done").textContent = done;
  document.getElementById("c-all").textContent = DATA.length;
  for (const k in tally) document.getElementById("c-"+k).textContent = tally[k];
  document.getElementById("bar").style.width = (100*done/DATA.length)+"%";
  try { localStorage.setItem(STORE, JSON.stringify(labels)); } catch(e) {}
}

document.addEventListener("keydown", e => {
  if (e.key in KEYS){
    const v = KEYS[e.key];
    if (v === null) delete labels[DATA[cur].code]; else labels[DATA[cur].code] = v;
    if (cur < DATA.length-1) cur++;
    paint();
    grid.children[cur].scrollIntoView({block:"nearest"});
    e.preventDefault();
  }
  if (e.key === "ArrowRight" && cur < DATA.length-1){ cur++; paint(); grid.children[cur].scrollIntoView({block:"nearest"}); }
  if (e.key === "ArrowLeft" && cur > 0){ cur--; paint(); grid.children[cur].scrollIntoView({block:"nearest"}); }
});

document.getElementById("save").onclick = () => {
  const out = DATA.map(d => ({code:d.code, lang:d.lang, label:labels[d.code] || null}));
  const b = new Blob([JSON.stringify(out,null,1)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(b); a.download = "panel_labels.json"; a.click();
};

paint();
grid.children[0].scrollIntoView({block:"nearest"});
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=Path, default=Path("sample.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("label.html"))
    ap.add_argument("--full-res", action="store_true",
                    help="link full-resolution images (slower to load, easier to judge)")
    args = ap.parse_args()

    if not args.sample.exists():
        raise SystemExit(f"not found: {args.sample} — run build_sample.py first")

    rows = []
    with args.sample.open(encoding="utf-8") as fh:
        for line in fh:
            p = json.loads(line)
            img = p["image_nutrition_url"]
            if args.full_res:
                img = img.replace(".400.jpg", ".full.jpg")
            rows.append({
                "code": p["code"],
                "name": (p.get("product_name") or "")[:44],
                "lang": p.get("lang") or "",
                "img": img,
            })

    args.out.write_text(PAGE.replace("__DATA__", json.dumps(rows)), encoding="utf-8")
    print(f"wrote {args.out} — {len(rows)} images")
    print("open it, label with 1/2/3/4, then click Download labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
