"""CLI entry point: incident-agent 'incident description'"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .agent import investigate


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="incident-agent",
        description="Investigate an incident using logs + GitHub history.",
    )
    parser.add_argument(
        "incident",
        nargs="?",
        help="Incident description. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Maximum agent loop iterations (default: 20).",
    )
    args = parser.parse_args()

    description = args.incident if args.incident else sys.stdin.read().strip()
    if not description:
        parser.error("No incident description provided.")

    final = investigate(description, max_iterations=args.max_iterations)
    print("\n" + "=" * 60)
    print("FINAL ANALYSIS")
    print("=" * 60)
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
