#!/usr/bin/env python3
"""
cli.py — Entry point for the Power Explained social media content producer.

Usage:
  python cli.py --source URL_OR_PATH
                --platform [bluesky|threads|linkedin|instagram|all]
                [--piece "Title"] [--queue]

Examples:
  python cli.py --source https://power-explained.jason-edelman.org/why-your-city-doesnt-work.html \
                --platform all

  python cli.py --queue
"""

import argparse
import os
import sys
from datetime import date

import yaml

import producer


def run_single(
    source: str,
    platforms: list[str],
    title: str | None,
    framework_context: str,
) -> str:
    """Process a single source URL or path. Returns the output markdown string."""
    print(f"[cli] Fetching source: {source}")
    source_text = producer.fetch_source_text(source)

    print("[cli] Step 1: extracting argument and claims…")
    argument = producer.extract_argument(source_text, framework_context)
    print(f"[cli] Argument extracted: {argument.get('thesis', '(no thesis)')}")
    print(f"[cli] Claims extracted: {len(argument.get('claims', []))}")

    print("[cli] Step 2: adversary review…")
    review = producer.adversary_review(argument)
    cleared = producer.build_cleared_argument(argument, review)
    dropped = len(cleared.get("high_risk_dropped", []))
    passed = len(cleared.get("claims", []))
    print(f"[cli] Adversary: {dropped} claims dropped, {passed} cleared")
    print(f"[cli] Overall credibility: {review.get('overall_credibility', '?')}")
    if review.get("biggest_risk"):
        print(f"[cli] Biggest risk: {review['biggest_risk']}")

    piece_title = title or argument.get("title", "Untitled")
    posts_by_platform: dict = {}

    for platform in platforms:
        print(f"[cli] Step 3: generating {platform} posts…")
        if platform == "instagram":
            result = producer.generate_instagram_slideshow(
                cleared, framework_context, source_url=source
            )
            posts_by_platform[platform] = result
            print(f"[cli] instagram: {len(result.get('slides', []))} slide(s) generated")
        else:
            posts = producer.generate_posts(
                cleared, platform, framework_context, source_url=source
            )
            posts_by_platform[platform] = posts
            print(f"[cli] {platform}: {len(posts)} post(s) generated")

    today = date.today().isoformat()
    return producer.format_output(
        piece_title, argument, review, cleared, posts_by_platform, today
    )


def write_output(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[cli] Output written to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Power Explained social media content producer"
    )
    parser.add_argument("--source", help="URL or path to the source piece")
    parser.add_argument(
        "--platform",
        choices=["bluesky", "threads", "linkedin", "instagram", "all"],
        default="all",
        help="Target platform(s) (default: all)",
    )
    parser.add_argument(
        "--piece",
        help="Title for the output filename (optional; defaults to extracted title)",
    )
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Process all pending items in queue.yaml",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (only used with --source, not --queue)",
    )

    args = parser.parse_args()

    if not args.source and not args.queue:
        parser.error("Either --source or --queue is required.")

    print("[cli] Fetching commons framework context…")
    framework_context = producer.fetch_framework_context()
    print("[cli] Framework context loaded.")

    if args.queue:
        queue_path = "queue.yaml"
        if not os.path.exists(queue_path):
            print(f"[cli] Error: {queue_path} not found.", file=sys.stderr)
            sys.exit(1)

        with open(queue_path, "r") as f:
            queue = yaml.safe_load(f)

        pieces = queue.get("pieces", [])
        pending = [p for p in pieces if p.get("status") == "pending"]

        if not pending:
            print("[cli] No pending items in queue.")
            return

        print(f"[cli] Processing {len(pending)} pending item(s)…")

        for item in pending:
            print(f"\n[cli] === Processing: {item['title']} ===")
            platforms = item.get("platforms", ["bluesky", "linkedin"])
            output_path = item.get("output_path", f"output/{item['id']}.md")

            try:
                content = run_single(
                    source=item["url"],
                    platforms=platforms,
                    title=item.get("title"),
                    framework_context=framework_context,
                )
                write_output(output_path, content)

                # Mark done in queue
                item["status"] = "done"
                item["output_path"] = output_path
            except Exception as e:
                print(f"[cli] Error processing {item['id']}: {e}", file=sys.stderr)
                item["status"] = "error"
                item["error"] = str(e)

        # Persist updated queue
        with open(queue_path, "w") as f:
            yaml.dump(queue, f, allow_unicode=True, sort_keys=False)
        print("\n[cli] Queue updated.")

    else:
        # Single source mode
        all_platforms = ["bluesky", "threads", "linkedin", "instagram"]
        platforms = all_platforms if args.platform == "all" else [args.platform]

        content = run_single(
            source=args.source,
            platforms=platforms,
            title=args.piece,
            framework_context=framework_context,
        )

        # Determine output path
        if args.output:
            out_path = args.output
        else:
            slug = args.piece or "output"
            slug = slug.lower().replace(" ", "-").replace("/", "-")
            slug = "".join(c for c in slug if c.isalnum() or c == "-")
            out_path = f"output/{slug}.md"

        write_output(out_path, content)
        print("\n--- Preview ---")
        print(content[:1000])
        if len(content) > 1000:
            print(f"\n[…{len(content) - 1000} more characters — see {out_path}]")


if __name__ == "__main__":
    main()
