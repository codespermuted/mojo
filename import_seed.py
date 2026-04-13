#!/usr/bin/env python3
"""Import seed knowledge from JSON file into Mojo database."""

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))

from db_ops import get_db, init_db, save_knowledge

console = Console()


def import_seed(seed_path: str, force: bool = False):
    """Import seed knowledge from a JSON file.
    
    Args:
        seed_path: Path to seed JSON file
        force: If True, overwrite existing entries with same ID
    """
    path = Path(seed_path)
    if not path.exists():
        console.print(f"[red]Seed file not found: {seed_path}[/red]")
        return

    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        console.print("[red]Seed file must be a JSON array[/red]")
        return

    # Ensure DB is initialized
    init_db()
    db = get_db()

    imported = 0
    skipped = 0

    for item in items:
        # Validate required fields
        required = ["id", "type", "domain", "title", "content"]
        missing = [f for f in required if f not in item]
        if missing:
            console.print(f"[yellow]Skipping item: missing {missing}[/yellow]")
            skipped += 1
            continue

        # Check if already exists
        existing = db.execute(
            "SELECT id FROM knowledge WHERE id = ?", (item["id"],)
        ).fetchone()

        if existing and not force:
            console.print(f"[dim]Exists, skipping: {item['id']}[/dim]")
            skipped += 1
            continue

        # Set defaults
        item.setdefault("confidence", 0.5)
        item.setdefault("approved", 0)
        item.setdefault("usage_count", 0)
        item.setdefault("source_session_id", "seed-import")
        item.setdefault("related_ids", [])
        item.setdefault("tags", [])
        item.setdefault("reasoning", "")

        save_knowledge(db, item)
        imported += 1

    db.close()

    # Summary
    console.print()
    table = Table(title="Seed Import Results")
    table.add_column("Metric", style="bold")
    table.add_column("Count")
    table.add_row("Imported", f"[green]{imported}[/green]")
    table.add_row("Skipped", f"[yellow]{skipped}[/yellow]")
    table.add_row("Total in file", str(len(items)))
    console.print(table)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import seed knowledge")
    parser.add_argument("seed_file", help="Path to seed JSON file")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing entries")
    args = parser.parse_args()

    import_seed(args.seed_file, force=args.force)


if __name__ == "__main__":
    main()
