"""
app.py — Streamlit UI for the Power Explained content producer.

Run with:
  streamlit run app.py
"""

import json
from datetime import date

import streamlit as st

import producer

st.set_page_config(
    page_title="Power Explained — Content Producer",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("Power Explained")
st.caption("Social media content producer — commons framework")

# ---------------------------------------------------------------------------
# Input form
# ---------------------------------------------------------------------------

with st.form("generate_form"):
    source = st.text_input(
        "Source URL or file path",
        placeholder="https://power-explained.jason-edelman.org/...",
    )
    platforms = st.multiselect(
        "Platforms",
        ["bluesky", "threads", "linkedin", "instagram"],
        default=["bluesky", "threads", "linkedin", "instagram"],
    )
    submitted = st.form_submit_button("Generate", type="primary")

if not submitted:
    st.stop()

if not source:
    st.error("A source URL or file path is required.")
    st.stop()

if not platforms:
    st.error("Select at least one platform.")
    st.stop()

# ---------------------------------------------------------------------------
# Generation — runs top-to-bottom with spinners, caches nothing
# (each run is a deliberate editorial act, not a lookup)
# ---------------------------------------------------------------------------

with st.spinner("Loading commons framework context…"):
    framework_context = producer.fetch_framework_context()

with st.spinner("Fetching source…"):
    try:
        source_text = producer.fetch_source_text(source)
    except Exception as e:
        st.error(f"Could not fetch source: {e}")
        st.stop()

with st.spinner("Step 1 — extracting argument structure…"):
    try:
        argument = producer.extract_argument(source_text, framework_context)
    except Exception as e:
        st.error(f"Argument extraction failed: {e}")
        st.stop()

piece_title = argument.get("title", "Untitled")

st.success(f"**{piece_title}** — argument extracted")

with st.expander("Argument structure"):
    st.json(argument)

# Step 2 — generate per platform
posts_by_platform: dict = {}
for platform in platforms:
    with st.spinner(f"Step 2 — generating {platform}…"):
        try:
            if platform == "instagram":
                posts_by_platform[platform] = producer.generate_instagram_slideshow(
                    argument, framework_context, source_url=source
                )
            else:
                posts_by_platform[platform] = producer.generate_posts(
                    argument, platform, framework_context, source_url=source
                )
        except Exception as e:
            st.error(f"{platform} generation failed: {e}")
            posts_by_platform[platform] = None

# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

CHAR_LIMITS = {"bluesky": 300, "threads": 500}


def render_thread(platform: str, posts: list[str]) -> None:
    limit = CHAR_LIMITS.get(platform)
    for i, post in enumerate(posts, 1):
        over = limit and len(post) > limit
        label = f"Post {i}" + (f" — {len(post)}/{limit} chars" if limit else "")
        if over:
            st.warning(f"Post {i} exceeds {limit} chars ({len(post)})")
        # st.code gives a built-in copy button
        st.code(post, language=None, wrap_lines=True)


def render_linkedin(posts: list[str]) -> None:
    text = "\n\n".join(posts)
    st.code(text, language=None, wrap_lines=True)


def render_instagram(manifest: dict) -> None:
    slides = manifest.get("slides", [])
    caption = manifest.get("caption", "")

    for i, slide in enumerate(slides, 1):
        col_img, col_text = st.columns([1, 2])
        with col_img:
            img_url = slide.get("image_url")
            if img_url:
                st.image(img_url, caption=slide.get("image_query", ""))
            else:
                st.markdown(
                    f"_No image found for: **{slide.get('image_query', '')}**_"
                )
        with col_text:
            st.markdown(f"**Slide {i}**")
            st.code(slide["text"], language=None, wrap_lines=True)
        st.divider()

    if caption:
        st.markdown("**Caption**")
        st.code(caption, language=None, wrap_lines=True)


# ---------------------------------------------------------------------------
# Output tabs
# ---------------------------------------------------------------------------

st.markdown("---")

tabs = st.tabs([p.title() for p in platforms])
for tab, platform in zip(tabs, platforms):
    with tab:
        content = posts_by_platform.get(platform)
        if content is None:
            st.error("Generation failed for this platform.")
        elif platform == "instagram":
            render_instagram(content)
        elif platform == "linkedin":
            render_linkedin(content)
        else:
            render_thread(platform, content)

# ---------------------------------------------------------------------------
# Download full markdown
# ---------------------------------------------------------------------------

st.markdown("---")
today = date.today().isoformat()
full_output = producer.format_output(piece_title, argument, posts_by_platform, today)
st.download_button(
    label="Download full output (.md)",
    data=full_output,
    file_name=f"{piece_title.lower().replace(' ', '-')}-{today}.md",
    mime="text/markdown",
)
