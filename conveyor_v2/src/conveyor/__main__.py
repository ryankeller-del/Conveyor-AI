"""Conveyor v2 entry point.

Usage:
    python -m conveyor          # Default (no-op until UI is built)
    python -m conveyor --help   # Show CLI options
"""

import argparse
import sys


def main() -> None:
    """CLI entry point. No business logic. Delegates to subsystems."""
    parser = argparse.ArgumentParser(
        prog="conveyor",
        description="Conveyor AI — multi-agent swarm orchestrator",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Start the Chainlit UI server",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit",
    )

    args = parser.parse_args()

    if args.version:
        print("conveyor v0.1.0 (Phase A — skeleton)")
        sys.exit(0)

    if args.ui:
        print("UI mode not yet implemented (Phase B)")
        sys.exit(1)

    parser.print_help()


def main_ui() -> None:
    """Chainlit entry point — called when running `conveyor-ui`."""
    print("UI not yet implemented. Use 'chainlit run src/conveyor/ui/chainlit_app.py' when ready.")
    sys.exit(1)


if __name__ == "__main__":
    main()
