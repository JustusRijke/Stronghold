"""One-shot backfill: copy InvenTree's BOM line notes onto an existing dataset.

    uv run python import_bom_notes.py <url> <username> <password> <settings.toml>

Unlike migrate_inventree.py this is non-destructive: it opens the data file as
the app does and only sets notes on BOM lines it can match. Matching is by
(parent part id, component part id), which works because the migration copied
every InvenTree pk verbatim as our own. A line InvenTree has no note for is
left alone -- this fills blanks, it never clears what someone typed here.

Like migrate_inventree.py this is a migration tool, not a product feature: do
not maintain it against later schema changes.
"""

import argparse
from pathlib import Path

import db
import inventree
from models import BomLine
from settings import Settings
from sqlalchemy import select


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("username")
    ap.add_argument("password")
    ap.add_argument("settings", type=Path, help="the deployment settings.toml")
    args = ap.parse_args()

    cfg = Settings(args.settings)
    db.init(cfg.path_of("db", "data_path"), cfg.get("db", "auto_commit"))

    bom = inventree.fetch_bom(args.url, args.username, args.password)
    notes = {
        (b["parent"], b["component"]): b["note"] for b in bom if b["note"] is not None
    }
    print(f"{len(bom)} InvenTree bom lines, {len(notes)} carrying a note")

    with db.session() as s:
        lines = {
            (line.parent_part_id, line.component_part_id): line
            for line in s.scalars(select(BomLine))
        }

    set_, unmatched, kept = 0, 0, 0
    for key, note in notes.items():
        line = lines.get(key)
        if line is None:
            unmatched += 1
            print(f"  no local bom line for parent {key[0]} component {key[1]}")
        elif line.note:
            kept += 1
            print(f"  bom line {line.id} already has a note, left alone: {line.note!r}")
        else:
            db.set_bomline_note(line.id, note)
            set_ += 1

    print(f"set {set_}, kept {kept}, unmatched {unmatched}")


if __name__ == "__main__":
    main()
