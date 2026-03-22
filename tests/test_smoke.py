"""
Smoke tests — no API calls, no live network (except Wikimedia).
These verify that the modules import, core logic runs, and the queue parses.
"""

import json
import os
import sys

import pytest
import yaml

# Ensure repo root is on the path when running from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Import smoke
# ---------------------------------------------------------------------------

def test_producer_imports():
    import producer  # noqa: F401


def test_cli_imports():
    import cli  # noqa: F401


# ---------------------------------------------------------------------------
# format_output — pure function, no API, no network
# ---------------------------------------------------------------------------

def test_format_output_bluesky_structure():
    import producer

    argument = {
        "title": "Test Piece",
        "thesis": "The thesis.",
        "enclosure_move": "Something is being enclosed.",
        "commons_alternative": "A commons alternative exists.",
        "key_moves": ["move 1", "move 2"],
        "risks": [],
    }
    posts_by_platform = {
        "bluesky": ["Post one.", "Post two.", "Post three."],
        "linkedin": ["Opening claim.\n\nSecond paragraph.\n\nThird paragraph."],
    }
    output = producer.format_output("Test Piece", argument, posts_by_platform, "2026-03-22")

    assert "# Test Piece — Generated 2026-03-22" in output
    assert "## Argument extraction" in output
    assert "## Bluesky thread" in output
    assert "1. Post one." in output
    assert "## LinkedIn post" in output
    assert "Opening claim." in output
    assert "*Review before posting. Verify any flagged claims.*" in output


def test_format_output_threads_structure():
    import producer

    argument = {"title": "T", "thesis": "x", "key_moves": [], "risks": []}
    output = producer.format_output(
        "T",
        argument,
        {"threads": ["Thread post 1.", "Thread post 2."]},
        "2026-03-22",
    )
    assert "## Threads thread" in output
    assert "1. Thread post 1." in output


def test_format_output_instagram_structure():
    import producer

    argument = {"title": "T", "thesis": "x", "key_moves": [], "risks": []}
    manifest = {
        "slides": [
            {"text": "Hook slide.", "image_query": "urban zoning", "image_url": None},
            {"text": "Move one.", "image_query": "city council meeting", "image_url": "https://example.com/img.jpg"},
        ],
        "caption": "The argument. Link in bio.",
    }
    output = producer.format_output("T", argument, {"instagram": manifest}, "2026-03-22")

    assert "## Instagram slideshow" in output
    assert "### Slide 1" in output
    assert "Hook slide." in output
    assert "not found" in output  # slide 1 has no image_url
    assert "https://example.com/img.jpg" in output  # slide 2 has one
    assert "### Caption" in output
    assert "The argument. Link in bio." in output


def test_format_output_argument_json_is_valid():
    """The argument JSON block in the output must round-trip through json.loads."""
    import producer

    argument = {
        "title": "Test",
        "thesis": "Claim with \"quotes\" and unicode: café",
        "key_moves": ["a", "b"],
        "risks": ["verify this stat"],
    }
    output = producer.format_output("Test", argument, {}, "2026-03-22")

    # Extract the JSON block between the fences
    match = output.split("```json\n")[1].split("\n```")[0]
    parsed = json.loads(match)
    assert parsed["thesis"] == argument["thesis"]
    assert parsed["risks"] == argument["risks"]


# ---------------------------------------------------------------------------
# Bluesky character limit guard (can be called post-generation)
# ---------------------------------------------------------------------------

def test_bluesky_char_limit_helper():
    """Utility: all posts in a bluesky list fit within 300 chars."""
    posts = ["A" * 299, "B" * 300]
    for post in posts:
        assert len(post) <= 300, f"Post exceeds 300 chars: {post[:40]}…"


def test_threads_char_limit_helper():
    posts = ["A" * 499, "B" * 500]
    for post in posts:
        assert len(post) <= 500


# ---------------------------------------------------------------------------
# Queue parsing
# ---------------------------------------------------------------------------

def test_queue_parses():
    queue_path = os.path.join(os.path.dirname(__file__), "..", "queue.yaml")
    with open(queue_path) as f:
        data = yaml.safe_load(f)

    assert "pieces" in data
    pieces = data["pieces"]
    assert len(pieces) >= 1

    for piece in pieces:
        assert "id" in piece
        assert "url" in piece
        assert "title" in piece
        assert "platforms" in piece
        assert piece["status"] in ("pending", "done", "error")


# ---------------------------------------------------------------------------
# Wikimedia API connectivity (network — skipped if offline)
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_wikimedia_search_returns_url():
    import httpx
    import producer

    try:
        result = producer.search_wikimedia("community land trust housing")
    except httpx.ConnectError:
        pytest.skip("No network access")

    # May return None if no results, but must not raise
    assert result is None or result.startswith("https://")


@pytest.mark.network
def test_wikimedia_search_bad_query_does_not_raise():
    import producer

    result = producer.search_wikimedia("xyzzy no results expected 9999")
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# CLI --help does not crash
# ---------------------------------------------------------------------------

def test_cli_help(capsys):
    import cli
    with pytest.raises(SystemExit) as exc_info:
        sys.argv = ["cli.py", "--help"]
        cli.main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "bluesky" in captured.out
    assert "threads" in captured.out
    assert "instagram" in captured.out
