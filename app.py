"""
app.py — Streamlit UI for The Lockout pipeline.

Run with:
  streamlit run app.py

Runs the four-stage pipeline against a TAP feed endpoint.
Shows each stage's output and full audit trail.
Human posts manually from the generated content.
"""

import json
import os
from datetime import date
from pathlib import Path

import streamlit as st

import pipeline

st.set_page_config(
    page_title="The Lockout — Pipeline",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("The Lockout")
    st.caption("Commons enclosure pipeline")

    st.markdown("---")
    st.subheader("Feed")

    tap_url = st.text_input(
        "TAP endpoint URL",
        value=os.environ.get("TAP_ENDPOINT_URL", ""),
        placeholder="https://your-tap.railway.app/feed",
        help="Leave empty to use mock feed for development.",
    )

    st.markdown("**Follows** (one handle per line)")
    follows_raw = st.text_area(
        "follows",
        value="scout-two.bsky.social",
        height=120,
        label_visibility="collapsed",
        help="Bluesky handles to include. Empty = all posts from feed.",
    )
    follows = [h.strip() for h in follows_raw.splitlines() if h.strip()]

    st.markdown("---")
    st.subheader("Platforms")
    platforms = st.multiselect(
        "Generate posts for",
        ["bluesky", "threads", "linkedin", "instagram"],
        default=["bluesky", "threads"],
    )

    st.markdown("---")
    run_btn = st.button("Run pipeline", type="primary", use_container_width=True)

    st.markdown("---")
    st.subheader("Audit logs")
    audit_dir = Path("audit")
    if audit_dir.exists():
        logs = sorted(audit_dir.glob("*.json"), reverse=True)[:10]
        if logs:
            selected_log = st.selectbox(
                "View past run",
                options=[l.name for l in logs],
                index=None,
                placeholder="Select a log…",
            )
        else:
            st.caption("No logs yet.")
            selected_log = None
    else:
        selected_log = None

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("The Lockout")
st.caption("Real-world events → verified claims → teachable moments → posts")

if not run_btn and not (selected_log if "selected_log" in dir() else None):
    st.info(
        "Configure the feed in the sidebar and click **Run pipeline** to start. "
        "No TAP endpoint yet? Leave the URL empty to run against the mock feed."
    )

# ---------------------------------------------------------------------------
# Audit log viewer (sidebar selection)
# ---------------------------------------------------------------------------

if "selected_log" in dir() and selected_log:
    st.markdown("---")
    st.subheader(f"Audit log: {selected_log}")
    log_path = audit_dir / selected_log
    try:
        log_data = json.loads(log_path.read_text())
        st.markdown(
            f"**Run ID:** `{log_data.get('run_id')}` &nbsp;|&nbsp; "
            f"**Started:** {log_data.get('started_at')} &nbsp;|&nbsp; "
            f"**Entries:** {len(log_data.get('entries', []))}"
        )
        for entry in log_data.get("entries", []):
            with st.expander(
                f"[{entry['stage']}] {entry.get('note', '')} — {entry['timestamp']}"
            ):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**Model:** {entry.get('model')}  ")
                    st.markdown(
                        f"**Tokens:** {entry['tokens']['input']} in / "
                        f"{entry['tokens']['output']} out"
                    )
                with col2:
                    st.markdown(f"**Stage:** `{entry['stage']}`")

                if entry.get("thinking"):
                    with st.expander("Thinking trace"):
                        for i, t in enumerate(entry["thinking"], 1):
                            st.markdown(f"**Block {i}:**")
                            st.text(t)

                if entry.get("tool_calls"):
                    with st.expander(f"Tool calls ({len(entry['tool_calls'])})"):
                        for tc in entry["tool_calls"]:
                            st.markdown(f"**Tool:** `{tc.get('tool')}`")
                            st.markdown(f"**Input:** `{tc.get('input')}`")
                            if tc.get("result"):
                                st.markdown("**Result (truncated):**")
                                st.text(str(tc["result"])[:500])

                st.markdown("**Output:**")
                if isinstance(entry.get("output"), (dict, list)):
                    st.json(entry["output"])
                else:
                    st.text(str(entry.get("output", ""))[:1000])

    except Exception as e:
        st.error(f"Could not read log: {e}")

# ---------------------------------------------------------------------------
# Pipeline run
# ---------------------------------------------------------------------------

if not run_btn:
    st.stop()

if not platforms:
    st.error("Select at least one platform.")
    st.stop()

st.markdown("---")

# Stage progress display
progress = st.container()
with progress:
    status = st.status("Running pipeline…", expanded=True)

results = []

with status:
    st.write("📡 Fetching feed…")
    posts = pipeline.fetch_feed(tap_url, follows)
    st.write(f"✓ {len(posts)} posts retrieved")

    st.write("🔍 Stage 1: filtering for relevance…")

try:
    results = pipeline.run_pipeline(
        follows=follows,
        platforms=platforms,
        tap_url=tap_url,
    )
    status.update(label=f"Pipeline complete — {len(results)} story/stories passed all gates", state="complete")
except Exception as e:
    status.update(label=f"Pipeline error: {e}", state="error")
    st.exception(e)
    st.stop()

if not results:
    st.warning(
        "No stories passed all gates this run. "
        "Check the audit log for details on what was filtered and why."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

st.markdown(f"## {len(results)} story/stories ready to post")

for i, result in enumerate(results, 1):
    st.markdown("---")
    st.subheader(f"{i}. {result['headline']}")

    col1, col2 = st.columns([2, 1])
    with col1:
        if result.get("source_url"):
            st.markdown(f"**Source:** [{result['source_url']}]({result['source_url']})")
        st.markdown(f"**Framework angle:** `{result.get('framework_angle', '—')}`")
        st.markdown(f"**Verified summary:** {result.get('verified_summary', '—')}")
    with col2:
        teach = result.get("teach", {})
        if teach.get("commons_alternative"):
            st.markdown("**Commons alternative:**")
            st.markdown(teach["commons_alternative"])

    # Teach reasoning
    if teach.get("reasoning"):
        with st.expander("Scout's teachability reasoning"):
            st.markdown(teach["reasoning"])
            if teach.get("suggested_hook"):
                st.markdown(f"**Suggested hook:** {teach['suggested_hook']}")

    # Generated posts by platform
    posts_by_platform = result.get("posts_by_platform", {})

    platform_tabs = st.tabs([p.capitalize() for p in posts_by_platform.keys()])
    for tab, (platform, content) in zip(platform_tabs, posts_by_platform.items()):
        with tab:
            if isinstance(content, list):
                if platform in ("bluesky", "threads"):
                    char_limit = 300 if platform == "bluesky" else 500
                    for j, post in enumerate(content, 1):
                        over = len(post) > char_limit
                        with st.container():
                            cols = st.columns([5, 1])
                            with cols[0]:
                                edited = st.text_area(
                                    f"Post {j}",
                                    value=post,
                                    height=100,
                                    key=f"{i}_{platform}_{j}",
                                    label_visibility="collapsed",
                                )
                            with cols[1]:
                                char_count = len(edited)
                                color = "red" if char_count > char_limit else "green"
                                st.markdown(
                                    f"<span style='color:{color};font-family:monospace;"
                                    f"font-size:0.8rem'>{char_count}/{char_limit}</span>",
                                    unsafe_allow_html=True,
                                )
                else:
                    # LinkedIn: single post
                    st.text_area(
                        "Post",
                        value=content[0] if content else "",
                        height=250,
                        key=f"{i}_{platform}_post",
                        label_visibility="collapsed",
                    )
            elif isinstance(content, dict) and "slides" in content:
                # Instagram slideshow
                st.markdown("**Caption:**")
                st.text_area(
                    "caption",
                    value=content.get("caption", ""),
                    height=100,
                    key=f"{i}_{platform}_caption",
                    label_visibility="collapsed",
                )
                st.markdown("**Slides:**")
                for k, slide in enumerate(content.get("slides", []), 1):
                    with st.expander(f"Slide {k}: {slide.get('text', '')[:50]}…"):
                        st.markdown(f"**Text:** {slide.get('text', '')}")
                        img_url = slide.get("image_url")
                        if img_url:
                            st.image(img_url, width=300)
                        else:
                            st.caption(
                                f"No image found — query: '{slide.get('image_query', '')}'"
                            )

    # Audit link
    if result.get("audit_path"):
        st.caption(f"Audit log: `{result['audit_path']}`")

# ---------------------------------------------------------------------------
# Download all output
# ---------------------------------------------------------------------------

st.markdown("---")

all_output = []
for result in results:
    all_output.append(f"# {result['headline']}\n")
    all_output.append(f"**Source:** {result.get('source_url', '—')}\n")
    all_output.append(f"**Verified summary:** {result.get('verified_summary', '—')}\n\n")
    for platform, content in result.get("posts_by_platform", {}).items():
        all_output.append(f"## {platform.capitalize()}\n")
        if isinstance(content, list):
            for j, post in enumerate(content, 1):
                all_output.append(f"{j}. {post}\n")
        all_output.append("\n")
    all_output.append("---\n\n")

st.download_button(
    label="Download all output (.md)",
    data="\n".join(all_output),
    file_name=f"lockout-{date.today().isoformat()}.md",
    mime="text/markdown",
)
