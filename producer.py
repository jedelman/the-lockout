"""
producer.py — Three-step social media content generation for Power Explained.

Step 1: extract_argument — articulates the argument structure and extracts all
        verifiable claims before any posts are written.

Step 2: adversary_review — a separate model call with an explicitly adversarial
        system prompt. Reviews every extracted claim for accuracy risk. Returns
        a claim-by-claim risk assessment. High-risk claims are dropped from
        generation entirely. Medium-risk claims are softened or attributed.
        The adversary's job is to be ruthless.

Step 3: generate_posts — writes platform-specific posts using only claims the
        adversary cleared. Dialogic structure throughout: the hook poses a
        contradiction from the reader's situation rather than asserting the
        thesis. The commons alternative always appears.
"""

import json
import re
import httpx
import anthropic

MODEL = "claude-opus-4-6"

FRAMEWORK_URL = "https://power-explained.jason-edelman.org/agent-context.html"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

FRAMEWORK_FALLBACK = """
The commons framework analyzes power through the lens of enclosure and commoning.

Core concepts:
- Enclosure: the process by which shared resources, governance, and collective life are
  privatized, commodified, or brought under hierarchical control. Enclosure is not a
  historical event but an ongoing political project.
- The commons: resources governed by the communities that use them, through rules those
  communities make themselves. Commons are not "unmanaged" resources — they are
  self-governed ones.
- Hardin's error: the "tragedy of the commons" thesis (1968) described open-access
  regimes, not commons. Actual commons have governance. Elinor Ostrom's Nobel-winning
  work documented this in hundreds of cases globally.
- Power analysis: who controls access? Who sets the terms? Who bears the cost when
  things go wrong? Follow the enclosure, not the rhetoric of efficiency or optimization.
- The alternative is already being built: participatory budgeting, community land trusts,
  platform cooperatives, open-source ecosystems, and mutual aid networks are live examples
  of commons governance at scale.

Analytical moves:
1. Name the enclosure: what was once shared or self-governed is now being captured.
2. Identify the captor and the cost-bearer.
3. Show the commons alternative — not hypothetical, already existing somewhere.
4. Do not soften the framework's claims to seem balanced. The precision is the politics.
"""

# ---------------------------------------------------------------------------
# Step 1: Argument extraction
# ---------------------------------------------------------------------------

STEP1_SYSTEM = """You are reading a piece from Power Explained, a site that analyzes power
structures, capitalism, and commons-based alternatives through a specific analytical
framework (provided in context).

Your job is to extract the argument structure AND every verifiable claim in the piece.
Do not summarize. Do not describe what the piece does.

Extract:
- title: the piece title
- thesis: the core claim, one sentence
- enclosure_move: what resource is being enclosed, by whom, at whose expense
- commons_alternative: what collective governance looks like here, and where it is
  already being built
- key_moves: list of 3-5 analytical steps the piece takes from thesis to alternative
- claims: a list of every verifiable claim in the piece. For each claim:
    - text: the claim as a single sentence
    - type: one of "statistic", "historical_fact", "named_entity", "causal_claim",
            "existence_claim" (something exists or is currently operating)
    - source_hint: any source the piece cites for this claim, or null
    - context: the sentence or passage in the piece where this claim appears

Output as JSON. No commentary outside the JSON object."""


def extract_argument(source_text: str, framework_context: str) -> dict:
    """Step 1: Force the model to articulate the argument structure and extract
    all verifiable claims before any posts are written."""
    system = f"{STEP1_SYSTEM}\n\nCommons framework context:\n\n{framework_context}"
    raw = _call_model(
        system=system,
        user_content=f"Extract the argument structure from this piece:\n\n{source_text}",
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Step 2: Adversary review
# ---------------------------------------------------------------------------

ADVERSARY_SYSTEM = """You are an adversarial fact-checker. Your job is to protect the
credibility of a social media account that posts political analysis. You are not
sympathetic to the framework. You are not trying to help make the posts sound good.
Your only job is to find every claim that could embarrass the account if it turned out
to be wrong, inflated, outdated, or unverifiable.

You will be given a list of claims extracted from a piece. For each claim, assess:

1. risk_level: one of:
   - "low" — well-established, widely corroborated, would be hard to dispute
   - "medium" — probably accurate but should be attributed or softened
     (e.g., "according to X" rather than stated as fact), or is accurate but
     requires more precision to be defensible
   - "high" — specific statistic, contested causal claim, or named-entity
     assertion that could easily be wrong, outdated, or disputed; DROP from
     posts entirely

2. concern: what specifically could be wrong or misleading about this claim

3. verification: what would you need to check to confirm it — specific source,
   database, or authoritative record

4. safe_version: if risk_level is "medium", rewrite the claim in a form that
   is defensible without verification (attribution, hedge, or more precise
   scoping). If "high" or "low", set to null.

Additionally assess:
- overall_credibility: your overall judgment of the piece's factual reliability
  ("high", "medium", "low")
- biggest_risk: the single claim most likely to blow up if posted unchecked

Output as JSON with keys: reviewed_claims (list), overall_credibility, biggest_risk.
No commentary outside the JSON object.

Be ruthless. A wrong statistic on social media is worse than no statistic at all.
The account's credibility depends on never posting a claim that can be fact-checked
into embarrassment. If in doubt, rate it high risk."""


def adversary_review(argument: dict) -> dict:
    """Step 2: Adversarial review of all extracted claims."""
    claims = argument.get("claims", [])
    if not claims:
        return {
            "reviewed_claims": [],
            "overall_credibility": "high",
            "biggest_risk": "No specific claims extracted.",
        }

    user_content = (
        "Review the following claims extracted from a Power Explained piece.\n\n"
        f"Piece thesis: {argument.get('thesis', '')}\n\n"
        f"Claims:\n\n{json.dumps(claims, indent=2)}"
    )

    raw = _call_model(system=ADVERSARY_SYSTEM, user_content=user_content)
    return json.loads(raw)


def build_cleared_argument(argument: dict, review: dict) -> dict:
    """Merge adversary review back into the argument structure.

    Returns argument dict with:
    - claims: only low/medium-risk claims, medium ones rewritten to safe_version
    - high_risk_dropped: list of dropped claims (for the output report)
    - biggest_risk, overall_credibility from the adversary
    """
    reviewed = review.get("reviewed_claims", [])
    risk_map = {c.get("text", ""): c for c in reviewed}

    cleared = []
    dropped = []

    for claim in argument.get("claims", []):
        text = claim.get("text", "")
        reviewed_claim = risk_map.get(text, {})
        risk = reviewed_claim.get("risk_level", "low")

        if risk == "high":
            dropped.append({
                "text": text,
                "concern": reviewed_claim.get("concern", ""),
            })
        elif risk == "medium":
            safe = reviewed_claim.get("safe_version")
            cleared.append({
                **claim,
                "text": safe if safe else text,
                "risk_level": "medium",
                "original_text": text,
            })
        else:
            cleared.append({**claim, "risk_level": "low"})

    result = {**argument, "claims": cleared}
    result["high_risk_dropped"] = dropped
    result["biggest_risk"] = review.get("biggest_risk", "")
    result["overall_credibility"] = review.get("overall_credibility", "")
    return result


# ---------------------------------------------------------------------------
# Step 3: Post generation — dialogic/Freirean structure
# ---------------------------------------------------------------------------

STEP3_SYSTEM_TEMPLATE = """You are a social media content producer working from the commons
framework (provided in context). You have been given an argument structure that has
already passed adversarial fact-checking. Only the claims in this structure are cleared
for use. Do not introduce any claims not present in the cleared argument structure.

Your posts follow a dialogic structure inspired by Paulo Freire's problem-posing method:
- The hook poses a contradiction from the reader's lived situation — not the thesis.
  The reader is the subject, not the recipient.
- The middle posts name the structure behind the contradiction, one move at a time.
- The final post surfaces the commons alternative: what is already being built, by whom,
  and where. This is not rhetorical. It is the point.

Rules:
- Every post must make an argument or pose a real contradiction. No teasers. No "Here's
  why this matters." No "Did you know."
- The hook is a situation the reader recognizes, not an assertion they are meant to accept.
  Open rather than close.
- Do not soften the framework's core claims to seem balanced.
- Do not add claims not present in the cleared argument structure provided.
- If a claim is marked risk_level "medium", use its text exactly as given — it has
  already been rewritten to its defensible form. Do not un-hedge it.
- No emoji. No buzzwords. No marketing language.
- Write in the register of the source site: analytical, direct, not preachy.
- The commons alternative must appear. That is the point of the whole piece.

Platform: {platform}"""

BLUESKY_INSTRUCTIONS = """Write a Bluesky thread of 5-7 posts about this piece.

Format rules:
- Post 1: the hook. Name a specific contradiction the reader is living with — something
  in their city, their housing, their work, their governance. Pose it as a situation,
  not a thesis. One or two sentences. No thread labels. Make the reader feel recognized
  before you make them think.
- Posts 2-(N-1): the argument, one analytical move at a time. Each post must stand alone.
  Name who benefits, who pays, what the mechanism is.
- Final post: the commons alternative — what is already being built, by whom, and where.
  Not hypothetical. Specific. Link to the source piece URL if available.
- Hard limit: 300 characters per post.

Output: a JSON array of strings, one string per post. No other text."""

THREADS_INSTRUCTIONS = """Write a Threads thread of 5-7 posts about this piece.

Format rules:
- Post 1: the hook. Name a specific contradiction the reader is living with. Pose it as
  a situation. One or two sentences. No thread labels. Make the reader feel recognized
  before you make them think.
- Posts 2-(N-1): the argument, one move at a time. Each post stands alone.
- Final post: the commons alternative — specific, already existing. Include source URL
  if available.
- Hard limit: 500 characters per post. Use the extra room for precision, not padding.

Output: a JSON array of strings, one string per post. No other text."""

LINKEDIN_INSTRUCTIONS = """Write a single LinkedIn post about this piece.

Format rules:
- Opening line: names a contradiction in professional or civic life the reader will
  recognize. Not a question. Not a thesis. A situation.
- 4-5 short paragraphs. No bullet points. No em-dash lists.
- Middle paragraphs: the argument. Name who benefits, who pays, what the mechanism is.
  Use only cleared claims, exactly as given.
- Second-to-last paragraph: the commons alternative — what's already being built,
  specifically.
- Closing line: source URL if available.
- Tone: rigorous. Not academic, not promotional.

Output: a single JSON string, paragraphs separated by blank lines (\\n\\n). No other text."""

INSTAGRAM_INSTRUCTIONS = """Write an Instagram carousel (slideshow) for this piece.

Format rules:
- 6-8 slides total.
- Slide 1: the hook. A short, specific situation the reader recognizes (15-25 words).
  Not a thesis. Not a question. A moment of recognition.
- Slides 2-(N-1): one analytical move per slide (15-30 words). Each stands alone.
  Use cleared claims exactly as given.
- Second-to-last slide: the commons alternative — specific institution or practice
  already operating, named.
- Final slide: source URL. Invitation to read the full piece.
- For each slide, provide an image_query: 3-5 words for Wikimedia Commons search.
  Documentary and factual. No stock-photo language.
- Caption (Instagram caption field): 2-3 sentences making the argument in full.
  Ends with "Link in bio." No hashtags.

Output as JSON:
{
  "slides": [
    {"text": "slide overlay text", "image_query": "wikimedia search terms"},
    ...
  ],
  "caption": "Post caption text. Link in bio."
}
No other text outside the JSON object."""


def generate_posts(
    cleared_argument: dict,
    platform: str,
    framework_context: str,
    source_url: str = "",
) -> list[str]:
    """Step 3: Write platform-specific posts from the adversary-cleared argument."""
    instructions_map = {
        "bluesky": BLUESKY_INSTRUCTIONS,
        "threads": THREADS_INSTRUCTIONS,
        "linkedin": LINKEDIN_INSTRUCTIONS,
    }
    platform_labels = {
        "bluesky": "Bluesky thread",
        "threads": "Threads thread",
        "linkedin": "LinkedIn",
    }

    system = (
        STEP3_SYSTEM_TEMPLATE.format(platform=platform_labels.get(platform, platform))
        + f"\n\nCommons framework context:\n\n{framework_context}"
    )

    arg_for_prompt = dict(cleared_argument)
    if source_url:
        arg_for_prompt["source_url"] = source_url

    user_content = (
        f"{instructions_map[platform]}\n\nCleared argument structure:\n\n"
        f"{json.dumps(arg_for_prompt, indent=2)}"
    )

    raw = _call_model(system=system, user_content=user_content)
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return [str(p) for p in parsed]
    return [str(parsed)]


def generate_instagram_slideshow(
    cleared_argument: dict,
    framework_context: str,
    source_url: str = "",
) -> dict:
    """Step 3 (Instagram variant): Generate a slide manifest with Wikimedia images."""
    system = (
        STEP3_SYSTEM_TEMPLATE.format(platform="Instagram carousel")
        + f"\n\nCommons framework context:\n\n{framework_context}"
    )

    arg_for_prompt = dict(cleared_argument)
    if source_url:
        arg_for_prompt["source_url"] = source_url

    user_content = (
        f"{INSTAGRAM_INSTRUCTIONS}\n\nCleared argument structure:\n\n"
        f"{json.dumps(arg_for_prompt, indent=2)}"
    )

    raw = _call_model(system=system, user_content=user_content)
    manifest = json.loads(raw)

    for slide in manifest.get("slides", []):
        query = slide.get("image_query", "")
        print(f"[producer] Wikimedia search: '{query}'")
        slide["image_url"] = search_wikimedia(query) if query else None

    return manifest


# ---------------------------------------------------------------------------
# Orchestration helper
# ---------------------------------------------------------------------------

def run_pipeline(
    source_text: str,
    framework_context: str,
    platforms: list[str],
    source_url: str = "",
) -> tuple[dict, dict, dict, dict]:
    """Run the full three-step pipeline.

    Returns: (argument, review, cleared_argument, posts_by_platform)
    """
    argument = extract_argument(source_text, framework_context)
    review = adversary_review(argument)
    cleared = build_cleared_argument(argument, review)

    posts_by_platform: dict = {}
    for platform in platforms:
        if platform == "instagram":
            posts_by_platform[platform] = generate_instagram_slideshow(
                cleared, framework_context, source_url=source_url
            )
        else:
            posts_by_platform[platform] = generate_posts(
                cleared, platform, framework_context, source_url=source_url
            )

    return argument, review, cleared, posts_by_platform


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_output(
    title: str,
    argument: dict,
    review: dict,
    cleared_argument: dict,
    posts_by_platform: dict,
    date_str: str,
) -> str:
    """Format full markdown output including adversary review report."""
    lines = [f"# {title} — Generated {date_str}", ""]

    lines += ["## Step 1 — Argument extraction", ""]
    lines += ["```json", json.dumps({
        k: v for k, v in argument.items() if k != "claims"
    }, indent=2), "```", ""]
    lines += [f"**Claims extracted:** {len(argument.get('claims', []))}", ""]

    lines += ["## Step 2 — Adversary review", ""]
    lines += [
        f"**Overall credibility:** {review.get('overall_credibility', 'unknown')}",
        "",
        f"**Biggest risk:** {review.get('biggest_risk', 'none identified')}",
        "",
    ]

    dropped = cleared_argument.get("high_risk_dropped", [])
    if dropped:
        lines += [f"**Dropped ({len(dropped)} high-risk claims):**", ""]
        for d in dropped:
            lines.append(f"- ~~{d['text']}~~")
            if d.get("concern"):
                lines.append(f"  *Concern: {d['concern']}*")
        lines.append("")

    medium_claims = [c for c in cleared_argument.get("claims", [])
                     if c.get("risk_level") == "medium"]
    if medium_claims:
        lines += [f"**Softened ({len(medium_claims)} medium-risk claims):**", ""]
        for c in medium_claims:
            lines.append(f"- ~~{c.get('original_text', '')}~~")
            lines.append(f"  → {c['text']}")
        lines.append("")

    low_count = len([c for c in cleared_argument.get("claims", [])
                     if c.get("risk_level") == "low"])
    lines += [f"**Cleared:** {low_count} low-risk claims passed through", ""]

    reviewed = review.get("reviewed_claims", [])
    verifiable = [c for c in reviewed
                  if c.get("risk_level") in ("low", "medium") and c.get("verification")]
    if verifiable:
        lines += ["### Verification checklist", ""]
        lines += ["*Before posting, confirm these are still current:*", ""]
        for c in verifiable:
            lines.append(f"- [ ] **{c['text']}**")
            lines.append(f"  → {c['verification']}")
        lines.append("")

    lines += ["## Step 3 — Generated posts", ""]

    for platform, content in posts_by_platform.items():
        if platform == "bluesky":
            lines += ["### Bluesky thread", ""]
            for i, post in enumerate(content, 1):
                over = " ⚠️ OVER LIMIT" if len(post) > 300 else ""
                lines.append(f"{i}. [{len(post)} chars{over}] {post}")
            lines.append("")
        elif platform == "threads":
            lines += ["### Threads thread", ""]
            for i, post in enumerate(content, 1):
                over = " ⚠️ OVER LIMIT" if len(post) > 500 else ""
                lines.append(f"{i}. [{len(post)} chars{over}] {post}")
            lines.append("")
        elif platform == "linkedin":
            lines += ["### LinkedIn post", ""]
            lines += content
            lines.append("")
        elif platform == "instagram":
            lines += ["### Instagram slideshow", ""]
            for i, slide in enumerate(content.get("slides", []), 1):
                lines.append(f"#### Slide {i}")
                lines.append(f"**Text:** {slide['text']}")
                img_url = slide.get("image_url")
                query = slide.get("image_query", "")
                if img_url:
                    lines.append(f"**Image:** {img_url}")
                    lines.append(f"**Query used:** {query}")
                else:
                    lines.append(f"**Image:** *not found — query: '{query}'*")
                lines.append("")
            caption = content.get("caption", "")
            if caption:
                lines += ["#### Caption", "", caption, ""]

    lines.append("---")
    lines.append("*Review checklist above before posting.*")
    lines.append("*Never post a high-risk claim. Verify checklist items.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def fetch_framework_context() -> str:
    try:
        response = httpx.get(FRAMEWORK_URL, timeout=15)
        response.raise_for_status()
        html = response.text
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            el = soup.select_one("#context-content")
            if el:
                return el.get_text(separator="\n", strip=True)
        except ImportError:
            pass
        stripped = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", stripped).strip()
    except Exception as e:
        print(f"[producer] Could not fetch framework context ({e}); using fallback.")
        return FRAMEWORK_FALLBACK


def fetch_source_text(url_or_path: str) -> str:
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        response = httpx.get(url_or_path, timeout=30)
        response.raise_for_status()
        html = response.text
    else:
        with open(url_or_path, "r", encoding="utf-8") as f:
            html = f.read()
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except ImportError:
        pass
    stripped = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", stripped).strip()


def search_wikimedia(query: str) -> str | None:
    try:
        r = httpx.get(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
                "gsrlimit": 1,
            },
            timeout=10,
            headers={"User-Agent": "power-explained-producer/1.0"},
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            imageinfo = page.get("imageinfo", [])
            if imageinfo:
                return imageinfo[0].get("url")
    except Exception as e:
        print(f"[producer] Wikimedia search failed for '{query}': {e}")
    return None


def _call_model(system: str, user_content: str) -> str:
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        final = stream.get_final_message()
    raw = next((b.text for b in final.content if b.type == "text"), "")
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[^\n]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
    return raw
