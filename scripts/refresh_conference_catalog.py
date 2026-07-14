#!/usr/bin/env python3
"""Refresh the bounded ACSOS 2026 URL catalog without rebuilding local facts."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_service.conference_live import (  # noqa: E402
    ConferencePageCache,
    ConferencePageFetcher,
    LiveSearchConfig,
    discover_catalog,
    write_catalog,
)


def main() -> int:
    """Discover relevant ACSOS pages and write the catalog JSON file."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=None, help="Output catalog path.")
    parser.add_argument("--cache", type=Path, default=None, help="Persistent page cache path.")
    parser.add_argument("--verbose", action="store_true", help="Enable informational logs.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    config = LiveSearchConfig.from_environment()
    if args.catalog is not None or args.cache is not None:
        config = LiveSearchConfig(
            **{
                **config.__dict__,
                "catalog_path": args.catalog or config.catalog_path,
                "cache_path": args.cache or config.cache_path,
            },
        )
    cache = ConferencePageCache(config.cache_path)
    fetcher = ConferencePageFetcher(config, cache)
    candidates = discover_catalog(config, fetcher)
    write_catalog(config.catalog_path, candidates)
    print(f"Wrote {len(candidates)} ACSOS URL candidates to {config.catalog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
