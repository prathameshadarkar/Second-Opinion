# Deploying the review queue

No API key, no inference, no quota. The app reads `app_data/eval.json` and
nothing else, which is why it is safe to make public.

## 1. Build the bundle from your results

```bash
cp ~/Downloads/sample.jsonl .
cp -r ~/Downloads/results .
python3 prepare_eval_app.py --arms baseline units strict units-full
```

Add `cot` to that list once the arm has run. It should print ~391 records; if
it says 100, it is still reading the fixture.

Check locally:

```bash
streamlit run eval_app.py
```

## 2. Update the run figures

`CORPUS` and `RUN` near the top of `eval_app.py` are hardcoded — they describe
the corpus and the extraction run rather than the sample, so they are not in
the bundle. Update them if you rebuild against a newer export.

## 3. Push and deploy

```bash
git add eval_app.py prepare_eval_app.py app_data .streamlit requirements.txt DEPLOY.md
git commit -m "Add review queue"
git push
```

[share.streamlit.io](https://share.streamlit.io) → New app → your repo → main
file `eval_app.py` → set a custom subdomain → Deploy.

**Commit `app_data/eval.json`.** Without it the app deploys and immediately
errors. It is small enough that this is safe.
