# The Lockout

Real-world events → verified claims → teachable moments → social posts.

A four-stage pipeline that consumes a Bluesky TAP feed, fact-checks stories,
applies the commons/enclosure framework, and generates auditable social media posts.

## Pipeline

| Stage | What happens |
|-------|-------------|
| 1. Filter | Claude reviews incoming posts for commons/enclosure relevance |
| 2. Verify | Claims extracted, web-searched, adversary-reviewed. Kill/hold/proceed verdict. |
| 3. Teach | Scout makes the teachability call. Full reasoning trace logged. |
| 4. Generate | Dialogic posts from verified facts only. No claims outside what was cleared. |

Every SDK call is logged to `audit/` — prompt, thinking trace, tool calls, output.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Set `TAP_ENDPOINT_URL` env var when the Railway TAP endpoint is live.
Leave unset to run against the mock feed for development.

## Architecture

- `pipeline.py` — four-stage pipeline with audit logging
- `producer.py` — post generation engine (called by Stage 4)
- `app.py` — Streamlit UI
- `audit/` — per-run JSON audit logs (gitignored except .gitkeep)
