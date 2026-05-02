"""CLI entry point: incident-agent 'incident description'"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent import investigate


def _setup_logging(log_path: Path) -> None:
    """Two handlers on the 'blamebot' logger:
    - Console (stdout): INFO and above — iteration headers, tool names, PR URL, final result.
    - File:            DEBUG and above — everything above plus full messages.append JSON.
    """
    logger = logging.getLogger("blamebot")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # don't double-print via the root logger

    fmt = logging.Formatter("%(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)


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

    # Set up timestamped log file before anything else runs
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"investigation-{ts}.log"
    _setup_logging(log_path)

    final, pr_url = investigate(description, max_iterations=args.max_iterations)

    print("\n" + "=" * 60)
    print("FINAL ANALYSIS")
    print("=" * 60)
    print(final)
    if pr_url:
        print("\n" + "=" * 60)
        print("PULL REQUEST")
        print("=" * 60)
        print(f"  {pr_url}")
        print("  Review and merge when ready.")

    print(f"\nLog saved → {log_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
