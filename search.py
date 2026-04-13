#!/usr/bin/env python3
"""Search knowledge base by keyword, domain, or tag."""

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).parent))

from db_ops import get_db

console = Console()

TYPE_ICONS = {
    "domain_rule": "📏", "architecture_decision": "🏗️",
    "debug_playbook": "🔧", "anti_pattern": "⚠️",
    "tool_preference": "🔨", "code_pattern": "📝",
}


def search(query: str, domain: str = None, type_filter: str = None):
    """Search knowledge by keyword in title/content/tags."""
    db = get_db()

    sql = """
        SELECT * FROM knowledge 
        WHERE archived = 0
          AND (title LIKE ? OR content LIKE ? OR tags LIKE ? OR reasoning LIKE ?)
    """
    params = [f"%{query}%"] * 4

    if domain:
        sql += " AND domain LIKE ?"
        params.append(f"{domain}%")
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)

    sql += " ORDER BY confidence DESC, usage_count DESC"
    rows = db.execute(sql, params).fetchall()
    db.close()

    if not rows:
        console.print(f'[dim]No results for "{query}"[/dim]')
        return

    console.print(f'[bold]{len(rows)} result(s) for "{query}"[/bold]\n')

    for row_ in rows:
        row = dict(row_)
        icon = TYPE_ICONS.get(row["type"], "📄")
        approved = " ✓" if row["approved"] else ""
        tags = json.loads(row["tags"]) if row["tags"] else []
        tag_str = " ".join(f"[dim]#{t}[/dim]" for t in tags[:5])

        console.print(Panel(
            f"{row['content']}\n\n"
            f"[italic]{row.get('reasoning', '')}[/italic]\n\n"
            f"{tag_str}",
            title=f"{icon} {row['title']}{approved}",
            subtitle=f"[dim]{row['domain']} | conf={row['confidence']:.2f} | "
                     f"used={row['usage_count']}[/dim]",
            border_style="blue" if row["approved"] else "dim",
        ))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Search Mojo knowledge")
    parser.add_argument("query", help="Search keyword")
    parser.add_argument("--domain", help="Filter by domain prefix")
    parser.add_argument("--type", dest="type_filter", help="Filter by type")
    args = parser.parse_args()

    search(args.query, args.domain, args.type_filter)


if __name__ == "__main__":
    main()
