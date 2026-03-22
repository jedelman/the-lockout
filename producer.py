"""
producer.py — Two-step social media content generation for Power Explained.

Step 1: extract_argument — forces the model to articulate the argument structure
        before writing any posts, preventing paraphrase mode.
Step 2: generate_posts — writes platform-specific posts from the argument structure.
"""

import json
import re
import httpx
import anthropic

MODEL = "claude-opus-4-6"

FRAMEWORK_URL = "https://power-explained.jason-edelman.org/agent-context.html"

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

STEP1_SYSTEM = """You are reading a piece from Power Explained, a site that analyzes power structures,
capitalism, and commons-based alternatives through a specific analytical framework
(provided in context).

Your job is to extract the argument structure from the source text. Do not summarize.
Do not describe what the piece does. Identify:
- The core claim (thesis)
- The enclosure move: what resource is being enclosed, by whom, at whose expense
- The commons alternative: what collective governance looks like here, and where it
  is already being built
- The key analytical moves the piece makes to get from thesis to alternative
- Any specific claims (names, statistics, organizations) that require verification
  before being repeated in social media posts

Output as JSON. No commentary outside the JSON object."""

STEP2_SYSTEM_TEMPLATE = """You are a social media content producer working from the commons framework
(provided in context). You have been given the extracted argument structure
from a Power Explained piece. Your job is to write {platform}-format posts
that are analytically precise, not promotional.

Rules:
- Every post must make an argument, not describe one.
- The hook is a claim, not a teaser.
- Do not soften the framework's core claims to seem balanced.
- Do not add claims not present in the argument structure provided.
- The commons alternative must appear. That is the point of the whole piece.
- If a claim in the argument structure is flagged as a verification risk,
  do not include it in the posts. Cut it rather than hedge it.
- No emoji. No buzzwords. No marketing language.
- Write in the register of the source site: analytical, direct, not preachy."""

BLUESKY_INSTRUCTIONS = """Write a Bluesky thread of 4–6 posts about this piece.

Format rules:
- Post 1: the hook. One claim, stated plainly. No "Did you know." No "Thread 🧵."
  No rhetorical questions. Assert the argument.
- Posts 2–N: the argument, one move at a time. Each post must stand alone if
  read out of context.
- Final post: the commons alternative. Where is it being built right now?
  Link to the source piece using the URL from the argument structure if available.
- Hard limit: 300 characters per post.

Output: a JSON array of strings, one string per post. No other text."""

LINKEDIN_INSTRUCTIONS = """Write a single LinkedIn post about this piece.

Format rules:
- Opening line: a claim, not a question, not a hook teaser.
- 3–4 short paragraphs. No bullet points. No em-dash lists.
- Third paragraph: the commons alternative — what's already being built.
- Closing line: link to the source piece using the URL from the argument structure
  if available.
- Tone: rigorous. Not academic, not promotional.

Output: a single JSON string containing the full post text, paragraphs separated
by blank lines (\\n\\n). No other text."""


def fetch_framework_context() -> str:
    """Fetch the commons framework context from the live URL.
    Falls back to the inline string if the request fails."""
    try:
        response = httpx.get(FRAMEWORK_URL, timeout=15)
        response.raise_for_status()
        html = response.text

        # Extract text content of #context-content
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            el = soup.select_one("#context-content")
            if el:
                return el.get_text(separator="\n", strip=True)
        except ImportError:
            pass

        # Fallback: strip all tags with regex if bs4 not available
        stripped = re.sub(r"<[^>]+>", " ", html)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return stripped

    except Exception as e:
        print(f"[producer] Could not fetch framework context ({e}); using fallback.")
        return FRAMEWORK_FALLBACK


def fetch_source_text(url_or_path: str) -> str:
    """Fetch and return the plain text of a source piece.
    Accepts a URL or a local file path."""
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
        # Remove script/style elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except ImportError:
        pass

    stripped = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", stripped).strip()


def extract_argument(source_text: str, framework_context: str) -> dict:
    """Step 1: Force the model to articulate the argument structure.

    Returns a dict with keys:
      title, thesis, enclosure_move, commons_alternative, key_moves, risks
    """
    client = anthropic.Anthropic()

    system = f"{STEP1_SYSTEM}\n\nCommons framework context:\n\n{framework_context}"

    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"Extract the argument structure from this piece:\n\n{source_text}",
            }
        ],
    ) as stream:
        final = stream.get_final_message()

    raw = next(
        (b.text for b in final.content if b.type == "text"),
        "",
    )

    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[^\n]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)

    return json.loads(raw)


def generate_posts(
    argument: dict,
    platform: str,
    framework_context: str,
    source_url: str = "",
) -> list[str]:
    """Step 2: Write platform-specific posts from the argument structure.

    Returns a list of post strings (Bluesky) or a single-element list (LinkedIn).
    """
    client = anthropic.Anthropic()

    platform_label = "Bluesky thread" if platform == "bluesky" else "LinkedIn"
    system = (
        STEP2_SYSTEM_TEMPLATE.format(platform=platform_label)
        + f"\n\nCommons framework context:\n\n{framework_context}"
    )

    instructions = BLUESKY_INSTRUCTIONS if platform == "bluesky" else LINKEDIN_INSTRUCTIONS

    # Inject source URL into argument if provided
    arg_for_prompt = dict(argument)
    if source_url:
        arg_for_prompt["source_url"] = source_url

    user_content = (
        f"{instructions}\n\nArgument structure:\n\n"
        f"{json.dumps(arg_for_prompt, indent=2)}"
    )

    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        final = stream.get_final_message()

    raw = next(
        (b.text for b in final.content if b.type == "text"),
        "",
    )

    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[^\n]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)

    parsed = json.loads(raw)

    if isinstance(parsed, list):
        return [str(p) for p in parsed]
    return [str(parsed)]


def format_output(
    title: str,
    argument: dict,
    posts_by_platform: dict[str, list[str]],
    date_str: str,
) -> str:
    """Format the full markdown output for a piece."""
    lines = [f"# {title} — Generated {date_str}", ""]

    lines += ["## Argument extraction", ""]
    lines += ["```json", json.dumps(argument, indent=2), "```", ""]

    for platform, posts in posts_by_platform.items():
        if platform == "bluesky":
            lines += ["## Bluesky thread", ""]
            for i, post in enumerate(posts, 1):
                lines.append(f"{i}. {post}")
            lines.append("")
        elif platform == "linkedin":
            lines += ["## LinkedIn post", ""]
            lines += posts
            lines.append("")

    lines.append("---")
    lines.append("*Review before posting. Verify any flagged claims.*")

    return "\n".join(lines)
