"""
Second Opinion — a review queue for nutrition-panel extraction.

Not a demo and not a leaderboard. A demo shows the model working once; a
leaderboard gives you an average you cannot act on. This shows every case the
model got wrong, with the label image beside the numbers, so a reader can look
at the panel and judge for themselves whether the model or the database is
wrong — because with crowdsourced ground truth it is genuinely sometimes the
database.

No API key, no inference, no quota: it reads app_data/eval.json and nothing
else, which is why it is safe to deploy publicly.

    python3 prepare_eval_app.py --arms baseline units strict cot units-full
    streamlit run eval_app.py
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pandas as pd
import streamlit as st

DATA = Path(__file__).parent / "app_data" / "eval.json"

# From build_sample.py over the September 2026 export, and the extraction logs.
# Kept here because they describe the corpus and the run, not the sample.
CORPUS = {"screened": 4_733_607, "with_image": 2_308_131, "per_100g": 612_944,
          "complete": 253_163, "candidates": 242_597, "labelled": 399, "readable": 391}
RUN = {"calls": 1173, "failures": 0, "tokens_in": 2_136_033, "tokens_out": 253_911,
       "minutes": 35}

st.set_page_config(page_title="Second Opinion", page_icon="◨",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""<style>
.card{border:1px solid rgba(128,140,160,.25);border-radius:12px;padding:18px 20px;margin-bottom:18px}
.chip{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.08em;
padding:3px 10px;border-radius:100px;margin-right:10px}
.hi{background:rgba(248,113,113,.16);color:#F87171}
.md{background:rgba(251,191,36,.16);color:#FBBF24}
.lo{background:rgba(110,124,144,.18);color:#94A3B8}
.meta{font-size:12px;color:#8494A8;font-family:ui-monospace,monospace}
.ttl{font-size:17px;font-weight:600;margin:6px 0 2px}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load() -> dict:
    if not DATA.exists():
        st.error("`app_data/eval.json` not found — run `prepare_eval_app.py` first.")
        st.stop()
    return json.loads(DATA.read_text())


d = load()
ARMS, FIELDS, RECS = d["arms"], d["fields"], d["records"]
NICE = {"energy_kcal": "energy (kcal)", "fat_g": "fat (g)",
        "saturated_fat_g": "saturated fat (g)", "carbohydrates_g": "carbohydrates (g)",
        "sugars_g": "sugars (g)", "protein_g": "protein (g)", "salt_g": "salt (g)"}


def close(a, b):
    return abs(a - b) <= max(0.1, abs(b) * 0.02)


def consensus_value(r, f):
    vals = [v.get(f) for v in r["arms"].values() if v.get(f) is not None]
    return statistics.median(vals) if vals else None


# --------------------------------------------------------------------------
# Triage — the card queue is built from these
# --------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def triage() -> list[dict]:
    """One entry per panel with at least one problem, with a reason written out.

    Severity is by kind of failure, not size of error: a kilojoule
    substitution is a systematic column misread and always high, while three
    fields drifting a few percent is not.
    """
    out = []
    for r in RECS:
        kj, wrong, suspect = [], [], []
        for f in FIELDS:
            c = r["consensus"].get(f)
            got, want = consensus_value(r, f), r["truth"].get(f)
            if c == "kj":
                kj.append((f, got, want))
            elif c is False and got is not None and want is not None:
                # A database value near zero against a large, confident model
                # reading is more likely a data-entry error than a misread.
                if want < 0.05 and got > 1:
                    suspect.append((f, got, want))
                else:
                    wrong.append((f, got, want))
        if not (kj or wrong or suspect):
            continue

        if kj:
            f, got, want = kj[0]
            sev, tag = "hi", "KILOJOULE SUBSTITUTION"
            title = "Read the kJ column as kcal"
            why = (f"The model returned **{got:.0f} kcal**; the database says "
                   f"**{want:.0f}**. That ratio is 4.18 — the kilojoule figure. "
                   "European panels print energy twice, and it took the larger "
                   "number. Look at the label: both are usually visible.")
        elif suspect:
            f, got, want = suspect[0]
            sev, tag = "md", "GROUND TRUTH SUSPECT"
            title = f"Database value for {NICE[f]} looks wrong"
            why = (f"The model read **{got:g}**; the database records "
                   f"**{want:g}** per 100g. For a packaged food that database "
                   "value is implausible — this is more likely a data-entry "
                   "error than a model failure. Open Food Facts is "
                   "self-reported and is not automatically the correct answer.")
        else:
            n = len(wrong)
            f, got, want = wrong[0]
            sev = "md" if n >= 3 else "lo"
            tag = f"{n} FIELD{'S' if n > 1 else ''} WRONG"
            title = f"Disagrees on {NICE[f]}" + (f" and {n-1} more" if n > 1 else "")
            why = (f"Consensus returned **{got:g}** against a database value of "
                   f"**{want:g}**. " + ("Several fields are off, which usually "
                   "means the panel itself is hard to read — check the image."
                   if n >= 3 else "A single field differs."))

        out.append({**r, "sev": sev, "tag": tag, "title": title, "why": why,
                    "n_bad": len(kj) + len(wrong) + len(suspect),
                    "kind": "kj" if kj else "suspect" if suspect else "wrong"})
    order = {"hi": 0, "md": 1, "lo": 2}
    return sorted(out, key=lambda x: (order[x["sev"]], -x["n_bad"]))


@st.cache_data(show_spinner=False)
def totals() -> dict:
    agree = [0, 0]
    dis = [0, 0]
    per_arm = {a: [0, 0] for a in ARMS}
    for r in RECS:
        for f in FIELDS:
            want = r["truth"].get(f)
            if want is None:
                continue
            for a in ARMS:
                v = (r["arms"].get(a) or {}).get(f)
                if v is None:
                    continue
                per_arm[a][1] += 1
                per_arm[a][0] += close(v, want)
            c = r["consensus"].get(f)
            if r["agree"].get(f) is True:
                agree[1] += 1
                agree[0] += c is True
            elif r["agree"].get(f) is False:
                dis[1] += 1
                dis[0] += c is True
    return {"agree": agree, "dis": dis, "per_arm": per_arm}


T = totals()
Q = triage()


@st.cache_data(show_spinner=False)
def by_language(min_fields: int = 30) -> list[dict]:
    acc, serv = {}, {}
    for r in RECS:
        lg = r["lang"] or "??"
        a, s = acc.setdefault(lg, [0, 0]), serv.setdefault(lg, [0, 0])
        s[1] += 1
        s[0] += r["style"] == "per-serving"
        for f in FIELDS:
            c = r["consensus"].get(f)
            if c is None:
                continue
            a[1] += 1
            a[0] += c is True
    return sorted(
        [{"language": lg, "panels": serv[lg][1], "fields": n,
          "accuracy": 100 * h / n,
          "per-serving %": 100 * serv[lg][0] / serv[lg][1] if serv[lg][1] else 0}
         for lg, (h, n) in acc.items() if n >= min_fields],
        key=lambda x: -x["fields"])


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Second Opinion")
    st.caption("Vision-model extraction of nutrition panels, on NVIDIA NIM")

    st.markdown("**Model**")
    st.caption("`meta/llama-3.2-11b-vision-instruct`")
    st.markdown("**Prompt variants**")
    st.caption("  \n".join(f"`{a}`" for a in ARMS))

    st.markdown("**This run**")
    st.caption(
        f"{len(RECS)} panels · {len(ARMS)} variants  \n"
        f"{RUN['calls']:,} NIM calls · {RUN['failures']} failures  \n"
        f"{(RUN['tokens_in']+RUN['tokens_out'])/1e6:.1f}M tokens · "
        f"{RUN['minutes']} min wall clock"
    )
    st.divider()

    langs = sorted({r["lang"] for r in RECS if r["lang"]})
    f_lang = st.multiselect("Language", langs)
    f_style = st.multiselect("Panel style", ["per-100g", "per-serving"])
    f_kind = st.multiselect(
        "Failure type",
        ["Kilojoule substitution", "Ground truth suspect", "Other disagreement"])
    st.divider()
    st.caption(
        "Ground truth is Open Food Facts — crowdsourced and self-reported. "
        "Where the model and the database differ, the database is not "
        "automatically right."
    )
    st.caption("[Case study](https://www.prathameshadarkar.com/second-opinion.html)")


def apply_filters(rows):
    if f_lang:
        rows = [r for r in rows if r["lang"] in f_lang]
    if f_style:
        rows = [r for r in rows if r["style"] in f_style]
    kinds = {"Kilojoule substitution": "kj", "Ground truth suspect": "suspect",
             "Other disagreement": "wrong"}
    if f_kind:
        want = {kinds[k] for k in f_kind}
        rows = [r for r in rows if r.get("kind") in want]
    return rows


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

st.title("Second Opinion")
st.caption("Can a vision model read a nutrition panel — and can you tell when it has?")

ag_h, ag_n = T["agree"]
di_h, di_n = T["dis"]
tot = ag_n + di_n
c = st.columns(4)
c[0].metric("Panels analysed", f"{len(RECS):,}")
c[1].metric("Fields extracted", f"{tot:,}")
c[2].metric("High confidence", f"{100*ag_h/ag_n:.0f}%" if ag_n else "—",
            f"{100*ag_n/tot:.0f}% of fields, variants agree" if tot else "",
            delta_color="off")
c[3].metric("Needs review", f"{100*di_n/tot:.0f}%" if tot else "—",
            f"{100*di_h/di_n:.0f}% accurate when they disagree" if di_n else "",
            delta_color="off")

tabs = st.tabs(["Review queue", "Every panel", "By language", "Method"])

# ---------------------------------------------------------------- queue ----
with tabs[0]:
    rows = apply_filters(Q)
    st.caption(
        f"{len(rows)} of {len(Q)} panels with at least one field the consensus "
        f"got wrong, ordered by severity. {len(RECS)-len(Q)} panels were clean."
    )
    if not rows:
        st.info("No panels match. Loosen the filters in the sidebar.")

    for r in rows[:40]:
        with st.container():
            st.markdown(
                f'<div class="card"><span class="chip {r["sev"]}">{r["tag"]}</span>'
                f'<span class="meta">{r["code"]} · {r["lang"] or "??"} · '
                f'{r["style"]}</span><div class="ttl">{r["title"]}</div></div>',
                unsafe_allow_html=True)
            a, b = st.columns([1, 1.6])
            with a:
                if r.get("image"):
                    st.image(r["image"])
            with b:
                st.markdown(r["why"])
                with st.expander("All seven fields"):
                    st.dataframe(
                        pd.DataFrame([{
                            "field": NICE[f],
                            "database": r["truth"].get(f),
                            "consensus": consensus_value(r, f),
                            "verdict": {True: "correct", False: "wrong",
                                        "kj": "kJ read as kcal"}.get(
                                            r["consensus"].get(f), "—"),
                        } for f in FIELDS]),
                        hide_index=True, use_container_width=True)
            st.divider()
    if len(rows) > 40:
        st.caption(f"Showing the first 40 of {len(rows)}.")

# ---------------------------------------------------------------- browse ---
with tabs[1]:
    rows = apply_filters(RECS)
    st.caption(f"{len(rows)} of {len(RECS)} panels match the current filters.")
    if rows:
        by_code = {r["code"]: r for r in rows}
        # No explicit key: with one, a stored selection survives a filter change
        # and can point at a code no longer in the options. Letting Streamlit
        # derive identity from the option list makes it reset instead.
        code = st.selectbox(
            "Panel", list(by_code),
            format_func=lambda c: f"{c} · {by_code[c]['lang'] or '??'} · {by_code[c]['style']}")
        r = by_code[code]
        a, b = st.columns([1, 1.5])
        with a:
            if r.get("image"):
                st.image(r["image"], caption="Open Food Facts, CC-BY-SA")
        with b:
            table = []
            for f in FIELDS:
                row = {"field": NICE[f], "database": r["truth"].get(f)}
                for arm in ARMS:
                    row[arm] = (r["arms"].get(arm) or {}).get(f)
                row["verdict"] = {True: "correct", False: "wrong",
                                  "kj": "kJ read as kcal"}.get(
                                      r["consensus"].get(f), "—")
                table.append(row)
            st.dataframe(pd.DataFrame(table), hide_index=True,
                         use_container_width=True)
    else:
        st.info("No panels match. Loosen the filters in the sidebar.")

# -------------------------------------------------------------- language ---
with tabs[2]:
    langs = by_language()
    if not langs:
        st.info("Not enough data per language to break out.")
    else:
        df = pd.DataFrame(langs)
        st.bar_chart(df.set_index("language")[["accuracy"]], horizontal=True)
        st.dataframe(
            df, hide_index=True, use_container_width=True,
            column_config={
                "accuracy": st.column_config.NumberColumn("Accuracy", format="%.1f%%"),
                "per-serving %": st.column_config.NumberColumn(
                    "Per-serving panels", format="%.0f%%"),
            })
        st.warning(
            "**Read the per-serving column before the accuracy column.** "
            "English-language products here are disproportionately American, "
            "and American panels state values per serving, which the model must "
            "convert. Where a language scores badly and also has a high "
            "per-serving share, the apparent language effect is largely a "
            "format effect. The two cannot be separated in this sample."
        )

# ---------------------------------------------------------------- method ---
with tabs[3]:
    k = CORPUS
    st.subheader("From 4.7 million products to 391 examples")
    st.dataframe(
        pd.DataFrame([
            {"stage": s, "products": n, "share": 100 * n / k["screened"]}
            for s, n in [("In the export", k["screened"]),
                         ("Carry a nutrition image", k["with_image"]),
                         ("…recorded per 100g", k["per_100g"]),
                         ("…all seven nutrients present", k["complete"]),
                         ("Usable candidates", k["candidates"])]]),
        hide_index=True, use_container_width=True,
        column_config={"share": st.column_config.NumberColumn(
            "Share of corpus", format="%.1f%%")})
    st.caption(
        "The binding constraint is not photography. Half the corpus carries a "
        "nutrition image — but three quarters of those record values per "
        "serving, where the stored figure and the printed one disagree by "
        "construction."
    )
    st.divider()
    a, b = st.columns(2)
    a.markdown(
        f"**Sampling**  \n{k['labelled']} products drawn from "
        f"{k['candidates']:,} candidates, stratified to take equal numbers per "
        "language. That gives equal power to test language effects and makes "
        "the sample deliberately unrepresentative of the corpus — so no "
        "corpus-level rate is quoted here.")
    b.markdown(
        f"**Labelling**  \nAll {k['labelled']} images checked by eye before any "
        "API call, because the nutrition-image slot is crowdsourced and does "
        f"not always hold a panel. {k['readable']} were readable and went "
        "forward; the rest were excluded rather than counted as model failures.")
    st.divider()
    st.markdown(
        f"**Extraction** — each image sent under {len(ARMS)} prompt variants at "
        f"temperature 0. {RUN['calls']:,} calls, {RUN['failures']} failures, "
        f"{RUN['minutes']} minutes. Scored field by field against the database "
        "within a 2% tolerance and a small absolute floor.")

st.divider()
st.caption(
    "Data: [Open Food Facts](https://world.openfoodfacts.org/data) (ODbL; images "
    "CC-BY-SA). Inference: `meta/llama-3.2-11b-vision-instruct` via NVIDIA NIM. "
    "An evaluation exercise — not intended to support dietary or clinical decisions."
)
