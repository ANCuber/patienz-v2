#!/usr/bin/env python3
"""Phase 1.5 log migration: ingest data/log/*.txt into DB shards.

This script is non-destructive by design: it never edits or deletes source files.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from util import db_store


def _sid_from_filename(path: Path):
    # Expected filenames: 20250212110342.txt
    stem = path.stem
    if stem.isdigit() and len(stem) >= 8:
        return stem
    return None


def main():
    parser = argparse.ArgumentParser(description="Import data/log text files into SQLite log shards")
    parser.add_argument("--log-dir", default="data/log", help="Directory containing log .txt files")
    parser.add_argument("--glob", default="*.txt", help="Glob for log files")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report only, no DB writes")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}")
        return 1

    files = sorted(log_dir.glob(args.glob))
    if not files:
        print(f"No log files matched {args.glob} in {log_dir}")
        return 0

    if not args.dry_run:
        db_store.init_db()

    total_files = 0
    total_lines = 0
    total_inserted = 0
    skipped = 0

    for path in files:
        if not path.is_file():
            continue
        sid = _sid_from_filename(path)
        if not sid:
            print(f"[SKIP] {path.name}: invalid SID filename")
            skipped += 1
            continue

        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        line_count = sum(1 for ln in lines if ln.rstrip("\n") != "")
        total_files += 1
        total_lines += line_count

        if args.dry_run:
            print(f"[DRY] {path.name}: {line_count} non-empty lines")
            continue

        inserted = db_store.ingest_log_lines(
            sid=sid,
            lines=lines,
            source_file=str(path),
        )
        total_inserted += inserted
        print(f"[OK] {path.name}: inserted {inserted}/{line_count}")

    mode = "DRY RUN" if args.dry_run else "IMPORT"
    print("=" * 50)
    print(f"{mode} summary")
    print(f"files processed: {total_files}")
    print(f"files skipped:   {skipped}")
    print(f"lines seen:      {total_lines}")
    if not args.dry_run:
        print(f"lines inserted:  {total_inserted}")
    print("source files were left intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
