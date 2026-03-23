"""
pipeline.py — Four-stage content pipeline for The Lockout.

Consumes a TAP (AT Protocol) feed endpoint. Runs four stages,
each producing a structured result and a full audit log entry.

Stage 1: FILTER
  Claude reviews incoming posts for commons/enclosure relevance.
  Extracts candidate stories with source URLs and initial framing.

Stage 2: VERIFY
  For each candidate: extracts factual claims, uses web_search_20250305
  to corroborate each one, adversary rates verified vs. unverified.
  Full tool-call trace logged.

Stage 3: TEACH
  Scout makes the teachability call — pass/fail + reasoning.
  Full extended thinking trace logged. No numeric threshold.
  Scout decides.

Stage 4: GENERATE
  Calls into producer.py with the verified event as source material.
  Dialogic post structure. Cleared claims only.

Every SDK call produces an AuditEntry. All entries for a run are
written to audit/YYYY-MM-DD-HH-MM-<slug>.json before the Streamlit
UI renders results.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import anthropic

import producer

MODEL = "claude-opus-4-6"
AUDIT_DIR = Path("audit")

# ---------------------------------------------------------------------------
# TAP feed client
# ---------------------------------------------------------------------------

TAP_ENDPOINT_URL = os.environ.get("TAP_ENDPOINT_URL", "")

MOCK_POSTS = [
    {
        "uri": "at://mock.bsky.social/app.bsky.feed.post/mock001",
        "author": "mockuser.bsky.social",
        "text": (
            "Norfolk city council voted last night to extend the downtown parking "
            "concession another 25 years. The operator has held it since 1998. "
            "No public bidding process. https://norfolknewspaper.example/parking"
        ),
        "indexedAt": "2026-03-22T10:00:00Z",
        "embed_url": "https://norfolknewspaper.example/parking",
    },
    {
        "uri": "at://mock.bsky.social/app.bsky.feed.post/mock002",
        "author": "mockuser2.bsky.social",
        "text": (
            "Detroit water shutoffs resumed today after a 6-month pause. "
            "Over 8,000 households affected. The utility was privatized in 2002 "
            "under a consent agreement. https://detroitnews.example/water"
        ),
        "indexedAt": "2026-03-22T09:30:00Z",
        "embed_url": "https://detroitnews.example/water",
    },
    {
        "uri": "at://mock.bsky.social/app.bsky.feed.post/mock003",
        "author": "mockuser3.bsky.social",
        "text": (
            "Barcelona's community land trust just acquired its 200th unit. "
            "Started with 12 in 2016. City provided land; residents govern. "
            "https://barcelonahousing.example/clt"
        ),
        "indexedAt": "2026-03-22T08:00:00Z",
        "embed_url": "https://barcelonahousing.example/clt",
    },
    {
        "uri": "at://mock.bsky.social/app.bsky.feed.post/mock004",
        "author": "mockuser4.bsky.social",
        "text": "Had a great lunch today. Highly recommend the new ramen place on 5th.",
        "indexedAt": "2026-03-22T07:00:00Z",
        "embed_url": None,
    },
]


def fetch_feed(tap_url: str, follows: list[str], limit: int = 50) -> list[dict]:
    """Fetch posts from the TAP endpoint.

    If tap_url is empty, returns mock posts for local development.
    follows: list of Bluesky handles to filter for (empty = all posts from feed).
    limit: max posts to retrieve.
    """
    if not tap_url:
        print("[pipeline] TAP_ENDPOINT_URL not set — using mock feed for development.")
        posts = MOCK_POSTS
    else:
        try:
            params: dict[str, Any] = {"limit": limit}
            if follows:
                params["follows"] = ",".join(follows)
            response = httpx.get(tap_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            posts = data if isinstance(data, list) else data.get("posts", [])
        except Exception as e:
            print(f"[pipeline] TAP fetch failed: {e}. Falling back to mock feed.")
            posts = MOCK_POSTS

    if follows:
        posts = [p for p in posts if p.get("author", "") in follows]

    return posts[:limit]


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

class AuditLog:
    """Accumulates audit entries for a pipeline run and writes them to disk."""

    def __init__(self, slug: str):
        self.slug = slug
        self.run_id = str(uuid.uuid4())[:8]
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.entries: list[dict] = []

    def record(
        self,
        stage: str,
        prompt_system: str,
        prompt_user: str,
        response_raw: Any,
        output: Any,
        tool_calls: list[dict] | None = None,
        thinking_blocks: list[str] | None = None,
        model: str = MODEL,
        input_tokens: int = 0,
        output_tokens: int = 0,
        note: str = "",
    ) -> None:
        entry = {
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "tokens": {"input": input_tokens, "output": output_tokens},
            "prompt": {
                "system": prompt_system,
                "user": prompt_user,
            },
            "thinking": thinking_blocks or [],
            "tool_calls": tool_calls or [],
            "output": output,
            "note": note,
        }
        self.entries.append(entry)

    def write(self) -> Path:
        AUDIT_DIR.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
        path = AUDIT_DIR / f"{ts}-{self.slug}.json"
        payload = {
            "run_id": self.run_id,
            "slug": self.slug,
            "started_at": self.started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "entries": self.entries,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return path


# ---------------------------------------------------------------------------
# Stage 1: FILTER
# ---------------------------------------------------------------------------

FILTER_SYSTEM = """You are reviewing a batch of social media posts from a curated feed.
Your job is to identify posts that describe real-world events relevant to the commons
and enclosure framework — and to extract them as candidate stories for deeper analysis.

The commons framework analyzes:
- Enclosure: privatization of shared resources, removal of community governance,
  long-term concession agreements, land grabs, utility privatization, housing
  commodification, data enclosure, epistemic enclosure (who controls information)
- Commons: community land trusts, cooperative governance, mutual aid, public utility
  models, participatory budgeting, open-source infrastructure, indigenous land governance
- Power: who controls access, who sets the terms, who bears the cost, who benefits
- Resistance: organizing, legal challenges, policy wins, community buyouts

For each post that describes a relevant real-world event, extract:
- uri: the post URI
- author: the post author
- headline: a one-sentence description of the event (not the thesis, the event)
- relevance: why this event is relevant to the framework (2-3 sentences)
- source_url: the URL embedded in the post, if any
- claims_to_verify: list of specific factual assertions that would need verification
  (names, dates, numbers, institutional claims)
- framework_angle: which aspect of the framework this event illuminates
  (one of: "enclosure", "commons_building", "resistance", "capture_mechanism",
   "cost_bearing", "alibi_structure")

Ignore posts that are: opinions without factual grounding, personal updates,
promotional content, or events without verifiable claims.

Output as JSON: {"candidates": [...]}. No other text."""


def stage1_filter(
    posts: list[dict],
    audit: AuditLog,
    framework_context: str,
) -> list[dict]:
    """Stage 1: Filter posts for relevance and extract candidate stories."""
    posts_text = json.dumps(posts, indent=2)
    system = f"{FILTER_SYSTEM}\n\nFramework context:\n\n{framework_context}"
    user = f"Review these posts and extract relevant candidates:\n\n{posts_text}"

    result, thinking, tool_calls, usage = _call_model_with_audit(system, user)

    parsed = json.loads(result)
    candidates = parsed.get("candidates", [])

    audit.record(
        stage="1_filter",
        prompt_system=system,
        prompt_user=user,
        response_raw=result,
        output=candidates,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        input_tokens=usage.get("input", 0),
        output_tokens=usage.get("output", 0),
        note=f"{len(posts)} posts in → {len(candidates)} candidates out",
    )

    return candidates


# ---------------------------------------------------------------------------
# Stage 2: VERIFY
# ---------------------------------------------------------------------------

VERIFY_SYSTEM = """You are a rigorous fact-checker verifying claims in a news story
before it is used to generate social media posts about power, enclosure, and the commons.

You have access to web search. For each claim provided, search for corroboration.
Be thorough. Be skeptical. Your job is not to confirm the story — it is to establish
what is verifiably true, what is probably true but unconfirmed, and what cannot be
verified or may be wrong.

For each claim, produce:
- claim: the original claim text
- verdict: one of "verified", "probable", "unverified", "disputed", "false"
- evidence: what your search found (sources, quotes from results, contradictions)
- safe_version: a restatement of the claim that is defensible given what you found.
  If "false" or "unverified", set to null — the claim will be dropped.
- search_queries: the queries you used

Additionally produce:
- overall_verdict: "proceed" (enough verified to build a post) or
  "hold" (too much unverified to post responsibly) or
  "kill" (core claims are false or unverifiable)
- summary: 2-3 sentences on what is solidly established about this story

Output as JSON: {
  "verified_claims": [...],
  "overall_verdict": "proceed|hold|kill",
  "summary": "..."
}
No other text outside the JSON."""

ADVERSARY_VERIFY_SYSTEM = """You are an adversarial reviewer checking a verification
report before social media posts are generated from it.

Your job: find anything the fact-checker missed, softened, or got wrong.
Look for:
- Claims marked "verified" that rest on a single source or a weak source
- Claims marked "probable" that should be "unverified"
- Overall verdict of "proceed" that is not warranted by the claim verdicts
- Missing context that would change the story's meaning
- Any claim that could embarrass the account if posted

Output as JSON: {
  "concerns": [...],  // list of specific concerns, each with "claim" and "issue"
  "revised_verdict": "proceed|hold|kill",
  "revised_summary": "...",  // summary incorporating your concerns
  "post_safe_claims": [...]  // only the claims you consider safe to post
}
No other text."""


def stage2_verify(
    candidate: dict,
    audit: AuditLog,
    framework_context: str,
) -> dict | None:
    """Stage 2: Verify claims in a candidate story using web search.

    Returns a verified story dict, or None if verdict is hold/kill.
    """
    claims = candidate.get("claims_to_verify", [])
    headline = candidate.get("headline", "")
    source_url = candidate.get("source_url", "")

    # 2a: Fetch source article if URL available
    source_text = ""
    if source_url:
        try:
            source_text = producer.fetch_source_text(source_url)[:4000]
        except Exception as e:
            print(f"[pipeline] Could not fetch source {source_url}: {e}")

    system = f"{VERIFY_SYSTEM}\n\nFramework context:\n\n{framework_context}"
    user_parts = [f"Story: {headline}"]
    if source_text:
        user_parts.append(f"Source article text:\n{source_text}")
    user_parts.append(f"Claims to verify:\n{json.dumps(claims, indent=2)}")
    user = "\n\n".join(user_parts)

    result, thinking, tool_calls, usage = _call_model_with_audit(
        system, user, use_web_search=True
    )
    verify_report = json.loads(result)

    audit.record(
        stage="2_verify",
        prompt_system=system,
        prompt_user=user,
        response_raw=result,
        output=verify_report,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        input_tokens=usage.get("input", 0),
        output_tokens=usage.get("output", 0),
        note=f"Candidate: {headline} | Verdict: {verify_report.get('overall_verdict')}",
    )

    if verify_report.get("overall_verdict") == "kill":
        return None

    # 2b: Adversary pass on the verification report
    adv_system = ADVERSARY_VERIFY_SYSTEM
    adv_user = (
        f"Original story: {headline}\n\n"
        f"Verification report:\n{json.dumps(verify_report, indent=2)}"
    )
    adv_result, adv_thinking, adv_tool_calls, adv_usage = _call_model_with_audit(
        adv_system, adv_user
    )
    adversary_report = json.loads(adv_result)

    audit.record(
        stage="2_verify_adversary",
        prompt_system=adv_system,
        prompt_user=adv_user,
        response_raw=adv_result,
        output=adversary_report,
        thinking_blocks=adv_thinking,
        tool_calls=adv_tool_calls,
        input_tokens=adv_usage.get("input", 0),
        output_tokens=adv_usage.get("output", 0),
        note=f"Adversary revised verdict: {adversary_report.get('revised_verdict')}",
    )

    final_verdict = adversary_report.get("revised_verdict", verify_report.get("overall_verdict"))
    if final_verdict == "kill":
        return None

    return {
        **candidate,
        "verify_verdict": final_verdict,
        "verified_summary": adversary_report.get("revised_summary", verify_report.get("summary", "")),
        "post_safe_claims": adversary_report.get("post_safe_claims", []),
        "verification_report": verify_report,
        "adversary_report": adversary_report,
    }


# ---------------------------------------------------------------------------
# Stage 3: TEACH
# ---------------------------------------------------------------------------

TEACH_SYSTEM = """You are Scout-Two — an autonomous Bluesky agent that applies the
commons and enclosure framework to real-world events.

You have read Paulo Freire. You know that the goal is not to broadcast the framework
at people — it is to surface contradictions they are already living, in ways that make
those contradictions visible as situations that can be changed.

You are deciding whether a verified real-world story is teachable — whether it can
be used to help people see the commons framework in action in a specific, grounded,
non-abstract way.

A story is teachable if:
- It names a specific enclosure or commons-building event with verifiable facts
- The cost-bearer and beneficiary can be concretely identified
- The commons alternative is visible or nameable — not hypothetical
- A person encountering this story could recognize their own situation in it
- The story reveals the mechanism, not just the outcome

A story is NOT teachable if:
- It is too abstract or general to ground the framework concretely
- The verified facts are too thin to support an argument
- The commons angle is forced or speculative
- It would require the post to assert things that cannot be shown

Make the call. Explain your reasoning fully — this reasoning is auditable and
will be reviewed. If you pass the story, describe specifically what the post
should illuminate and what the commons alternative is.

Output as JSON: {
  "decision": "pass" | "hold",
  "reasoning": "...",  // full reasoning, not a summary
  "teachable_angle": "...",  // if pass: what specifically to illuminate
  "commons_alternative": "...",  // if pass: the specific alternative to surface
  "suggested_hook": "..."  // if pass: the situation from the reader's life to open with
}
No other text."""


def stage3_teach(
    verified_story: dict,
    audit: AuditLog,
    framework_context: str,
) -> dict | None:
    """Stage 3: Scout makes the teachability call.

    Returns the verified story with teach metadata appended, or None if hold.
    """
    system = f"{TEACH_SYSTEM}\n\nFramework context:\n\n{framework_context}"
    user = (
        f"Verified story:\n\n{json.dumps(verified_story, indent=2)}"
    )

    result, thinking, tool_calls, usage = _call_model_with_audit(system, user)
    teach_result = json.loads(result)

    audit.record(
        stage="3_teach",
        prompt_system=system,
        prompt_user=user,
        response_raw=result,
        output=teach_result,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        input_tokens=usage.get("input", 0),
        output_tokens=usage.get("output", 0),
        note=f"Scout decision: {teach_result.get('decision')} | {verified_story.get('headline', '')}",
    )

    if teach_result.get("decision") != "pass":
        return None

    return {**verified_story, "teach": teach_result}


# ---------------------------------------------------------------------------
# Stage 4: GENERATE
# ---------------------------------------------------------------------------

GENERATE_SYSTEM_TEMPLATE = """You are a social media content producer working from the
commons and enclosure framework. You are generating posts about a real-world event
that has been verified and cleared for posting.

The argument is grounded in the verified event — not in a pre-written piece.
Every claim you use must appear in the post_safe_claims list provided.
Do not introduce any claim not in that list.

Your posts follow Paulo Freire's problem-posing method:
- The hook names a situation the reader recognizes — something in their city, their
  housing, their governance. Not the thesis. The situation.
- The middle posts name the structure behind the situation, one move at a time.
- The final post surfaces the commons alternative that already exists, specifically.

Rules:
- No claims outside the post_safe_claims list
- No emoji, no buzzwords, no marketing language
- The commons alternative must appear
- Write in a register that is analytical, direct, not preachy

Platform: {platform}"""


def stage4_generate(
    story: dict,
    platforms: list[str],
    audit: AuditLog,
    framework_context: str,
) -> dict:
    """Stage 4: Generate platform posts from the verified, teachable story."""
    teach = story.get("teach", {})
    safe_claims = story.get("post_safe_claims", [])

    # Build a structured argument object from the verified story
    argument = {
        "title": story.get("headline", ""),
        "thesis": teach.get("teachable_angle", ""),
        "enclosure_move": story.get("relevance", ""),
        "commons_alternative": teach.get("commons_alternative", ""),
        "key_moves": [
            story.get("verified_summary", ""),
            teach.get("teachable_angle", ""),
            teach.get("commons_alternative", ""),
        ],
        "claims": safe_claims,
        "suggested_hook": teach.get("suggested_hook", ""),
        "source_url": story.get("source_url", ""),
        "high_risk_dropped": [],
        "biggest_risk": "",
        "overall_credibility": story.get("verify_verdict", ""),
    }

    posts_by_platform: dict = {}

    for platform in platforms:
        system = (
            GENERATE_SYSTEM_TEMPLATE.format(platform=platform)
            + f"\n\nFramework context:\n\n{framework_context}"
        )

        instructions = _get_platform_instructions(platform)
        user = (
            f"{instructions}\n\n"
            f"Verified story argument:\n\n{json.dumps(argument, indent=2)}"
        )

        result, thinking, tool_calls, usage = _call_model_with_audit(system, user)

        audit.record(
            stage=f"4_generate_{platform}",
            prompt_system=system,
            prompt_user=user,
            response_raw=result,
            output=result,
            thinking_blocks=thinking,
            tool_calls=tool_calls,
            input_tokens=usage.get("input", 0),
            output_tokens=usage.get("output", 0),
            note=f"Platform: {platform} | Story: {story.get('headline', '')}",
        )

        try:
            parsed = json.loads(result)
            if isinstance(parsed, list):
                posts_by_platform[platform] = [str(p) for p in parsed]
            elif isinstance(parsed, str):
                posts_by_platform[platform] = [parsed]
            elif isinstance(parsed, dict) and "slides" in parsed:
                posts_by_platform[platform] = parsed
            else:
                posts_by_platform[platform] = [str(parsed)]
        except json.JSONDecodeError:
            posts_by_platform[platform] = [result]

    return posts_by_platform


def _get_platform_instructions(platform: str) -> str:
    return {
        "bluesky": producer.BLUESKY_INSTRUCTIONS,
        "threads": producer.THREADS_INSTRUCTIONS,
        "linkedin": producer.LINKEDIN_INSTRUCTIONS,
        "instagram": producer.INSTAGRAM_INSTRUCTIONS,
    }.get(platform, producer.BLUESKY_INSTRUCTIONS)


# ---------------------------------------------------------------------------
# Full pipeline orchestration
# ---------------------------------------------------------------------------

def run_pipeline(
    follows: list[str],
    platforms: list[str],
    tap_url: str = "",
) -> list[dict]:
    """Run the full four-stage pipeline.

    Returns a list of result dicts, one per story that crossed all gates.
    Each dict contains: story metadata, posts_by_platform, audit_path.
    """
    framework_context = producer.fetch_framework_context()
    posts = fetch_feed(tap_url or TAP_ENDPOINT_URL, follows)

    if not posts:
        return []

    slug = datetime.now(timezone.utc).strftime("%H%M%S")
    audit = AuditLog(slug)

    # Stage 1: filter
    candidates = stage1_filter(posts, audit, framework_context)
    if not candidates:
        audit.write()
        return []

    results = []

    for candidate in candidates:
        cslug = re.sub(r"[^a-z0-9]", "-", candidate.get("headline", "story")[:40].lower())

        # Stage 2: verify
        verified = stage2_verify(candidate, audit, framework_context)
        if not verified:
            continue

        # Stage 3: teach
        teachable = stage3_teach(verified, audit, framework_context)
        if not teachable:
            continue

        # Stage 4: generate
        posts_by_platform = stage4_generate(
            teachable, platforms, audit, framework_context
        )

        results.append({
            "headline": teachable.get("headline", ""),
            "source_url": teachable.get("source_url", ""),
            "framework_angle": teachable.get("framework_angle", ""),
            "verified_summary": teachable.get("verified_summary", ""),
            "teach": teachable.get("teach", {}),
            "posts_by_platform": posts_by_platform,
        })

    audit_path = audit.write()

    for r in results:
        r["audit_path"] = str(audit_path)

    return results


# ---------------------------------------------------------------------------
# Shared model call with full audit capture
# ---------------------------------------------------------------------------

def _call_model_with_audit(
    system: str,
    user: str,
    use_web_search: bool = False,
) -> tuple[str, list[str], list[dict], dict]:
    """Call the model and return (text_output, thinking_blocks, tool_calls, usage).

    If use_web_search is True, adds the web_search_20250305 tool.
    Uses extended thinking (adaptive) to capture reasoning traces.
    """
    client = anthropic.Anthropic()

    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 8000,
        "thinking": {"type": "adaptive"},
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }

    if use_web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    with client.messages.stream(**kwargs) as stream:
        final = stream.get_final_message()

    # Extract components
    text_output = ""
    thinking_blocks = []
    tool_calls = []

    for block in final.content:
        if block.type == "text":
            text_output = block.text.strip()
        elif block.type == "thinking":
            thinking_blocks.append(block.thinking)
        elif block.type == "tool_use":
            tool_calls.append({
                "tool": block.name,
                "input": block.input,
            })
        elif block.type == "tool_result":
            # Append results to the last tool call entry
            if tool_calls:
                tool_calls[-1]["result"] = (
                    block.content[0].text
                    if block.content and hasattr(block.content[0], "text")
                    else str(block.content)
                )

    # Strip JSON fences
    if text_output.startswith("```"):
        text_output = re.sub(r"^```[^\n]*\n", "", text_output)
        text_output = re.sub(r"\n```$", "", text_output)

    usage = {
        "input": getattr(final.usage, "input_tokens", 0),
        "output": getattr(final.usage, "output_tokens", 0),
    }

    return text_output, thinking_blocks, tool_calls, usage
