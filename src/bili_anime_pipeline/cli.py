from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import PipelineError, build_episode


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a narrated anime short locally.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Render one episode.")
    build.add_argument("--episode", required=True, type=Path, help="Episode directory.")
    build.add_argument("--config", required=True, type=Path, help="Scene YAML, relative to --episode or absolute.")
    build.add_argument("--keep-temp", action="store_true", help="Keep intermediates after validation.")
    build.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    args = parser.parse_args()

    try:
        build_episode(args.episode, args.config, args.keep_temp, args.dry_run)
    except PipelineError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

