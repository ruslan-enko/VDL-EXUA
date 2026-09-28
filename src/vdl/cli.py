import argparse
import sys
from typing import Optional

from vdl.ui.app import VDLApp

VERSION = "0.1.0"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vdl",
        description="vdl — Minimalist terminal video downloader",
        add_help=True,
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Optional URL of the video page to download directly",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"vdl {VERSION}",
    )

    args = parser.parse_args()

    try:
        app = VDLApp(initial_url=args.url)
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
