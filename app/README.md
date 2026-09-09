# Review queue

A Streamlit app that shows every panel the model got wrong, with the label image
beside the numbers — so a reader can look at the evidence and judge whether the
model or the database is wrong.

**No API key, no inference, no quota.** It reads `app_data/eval.json` and
nothing else, which is why it is safe to deploy publicly.

```bash
python3 prepare_eval_app.py --arms baseline units strict cot units-full
streamlit run eval_app.py
```

See [DEPLOY.md](DEPLOY.md) for Streamlit Community Cloud.

`CORPUS` and `RUN` near the top of `eval_app.py` are hardcoded — they describe
the corpus and the extraction run rather than the sample, so they are not in the
bundle. Update them if you rebuild against a newer export.
