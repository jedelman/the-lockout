# The Lockout

Real-world events → verified claims → teachable moments → social posts.

A local editorial tool. Run it on your laptop when you want to generate posts.
Not a service. Not deployed. Yours.

## Pipeline

| Stage | What happens |
|-------|-------------|
| 1. Filter | Claude reviews incoming Bluesky posts for commons/enclosure relevance |
| 2. Verify | Claims extracted, web-searched, adversary-reviewed. Kill/hold/proceed verdict. |
| 3. Teach | Scout makes the teachability call. Full reasoning trace logged. |
| 4. Generate | Dialogic posts from verified facts only. No claims outside what was cleared. |

Every SDK call is logged to `audit/` — prompt, thinking trace, tool calls, output.

## Run

```bash
pip install -r requirements.txt
./run.sh
```

Or directly:

```bash
streamlit run app.py
```

## TAP feed

The pipeline reads from a Bluesky TAP endpoint. Set the URL in the Streamlit
sidebar, or via env var:

```bash
export TAP_ENDPOINT_URL=http://localhost:PORT/feed
./run.sh
```

Leave unset to run against the mock feed for development.

## Architecture

- `pipeline.py` — four-stage pipeline with audit logging
- `producer.py` — post generation engine (Stage 4)
- `app.py` — Streamlit UI
- `audit/` — per-run JSON audit logs (gitignored except .gitkeep)
