#!/usr/bin/env python3
"""Review and approve/reject extracted knowledge items."""

import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

sys.path.insert(0, str(Path(__file__).parent))

from db_ops import get_db, get_all_knowledge

console = Console()

TYPE_ICONS = {
    "domain_rule": "📏",
    "architecture_decision": "🏗️",
    "debug_playbook": "🔧",
    "anti_pattern": "⚠️",
    "tool_preference": "🔨",
    "code_pattern": "📝",
}


def review_pending():
    """Interactive review of unapproved knowledge items."""
    db = get_db()
    items = db.execute(
        "SELECT * FROM knowledge WHERE approved = 0 AND archived = 0 "
        "ORDER BY confidence DESC"
    ).fetchall()

    if not items:
        console.print("[green]No pending items to review.[/green]")
        db.close()
        return

    console.print(f"\n[bold]{len(items)} item(s) pending review[/bold]\n")

    for i, row in enumerate(items):
        item = dict(row)
        icon = TYPE_ICONS.get(item["type"], "📄")

        console.print(Panel(
            f"[bold]{icon} {item['title']}[/bold]\n"
            f"[dim]ID: {item['id']} | Domain: {item['domain']} | "
            f"Type: {item['type']} | Confidence: {item['confidence']}[/dim]\n\n"
            f"{item['content']}\n\n"
            f"[italic]Reasoning: {item.get('reasoning', 'N/A')}[/italic]",
            title=f"[{i+1}/{len(items)}]",
            border_style="blue",
        ))

        action = Prompt.ask(
            "[a]pprove / [e]dit / [r]eject / [s]kip / [q]uit",
            choices=["a", "e", "r", "s", "q"],
            default="s",
        )

        if action == "a":
            db.execute("UPDATE knowledge SET approved = 1 WHERE id = ?", (item["id"],))
            db.commit()
            console.print("[green]✓ Approved[/green]\n")

        elif action == "e":
            new_content = Prompt.ask("New content", default=item["content"])
            new_reasoning = Prompt.ask("New reasoning", default=item.get("reasoning", ""))
            db.execute(
                "UPDATE knowledge SET content = ?, reasoning = ?, approved = 1 WHERE id = ?",
                (new_content, new_reasoning, item["id"])
            )
            db.commit()
            console.print("[green]✓ Updated & approved[/green]\n")

        elif action == "r":
            db.execute("UPDATE knowledge SET archived = 1 WHERE id = ?", (item["id"],))
            db.commit()
            console.print("[red]✗ Archived[/red]\n")

        elif action == "q":
            break

    db.close()


def list_knowledge(domain: str = None, type_filter: str = None,
                   approved_only: bool = False):
    """List knowledge items in a table."""
    db = get_db()

    query = "SELECT * FROM knowledge WHERE archived = 0"
    params = []

    if domain:
        query += " AND domain LIKE ?"
        params.append(f"{domain}%")
    if type_filter:
        query += " AND type = ?"
        params.append(type_filter)
    if approved_only:
        query += " AND approved = 1"

    query += " ORDER BY domain, confidence DESC"
    items = db.execute(query, params).fetchall()
    db.close()

    if not items:
        console.print("[dim]No items found.[/dim]")
        return

    table = Table(title=f"Knowledge Base ({len(items)} items)")
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="cyan", width=16)
    table.add_column("Domain", style="blue", width=20)
    table.add_column("Type", width=12)
    table.add_column("Title", width=35)
    table.add_column("Conf", justify="right", width=5)
    table.add_column("Used", justify="right", width=5)
    table.add_column("✓", width=2)

    for i, row in enumerate(items):
        icon = TYPE_ICONS.get(row["type"], "")
        approved = "✓" if row["approved"] else ""
        table.add_row(
            str(i + 1),
            row["id"],
            row["domain"],
            f"{icon} {row['type'][:10]}",
            row["title"][:35],
            f"{row['confidence']:.2f}",
            str(row["usage_count"]),
            approved,
        )

    console.print(table)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Review Mojo knowledge")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("pending", help="Review pending (unapproved) items")

    list_p = sub.add_parser("list", help="List all knowledge")
    list_p.add_argument("--domain", help="Filter by domain prefix")
    list_p.add_argument("--type", dest="type_filter", help="Filter by type")
    list_p.add_argument("--approved", action="store_true", help="Approved only")

    approve_p = sub.add_parser("approve", help="Approve item by ID")
    approve_p.add_argument("item_id", help="Knowledge item ID")

    reject_p = sub.add_parser("reject", help="Archive item by ID")
    reject_p.add_argument("item_id", help="Knowledge item ID")

    args = parser.parse_args()

    if args.cmd == "pending" or args.cmd is None:
        review_pending()
    elif args.cmd == "list":
        list_knowledge(args.domain, args.type_filter, args.approved)
    elif args.cmd == "approve":
        db = get_db()
        db.execute("UPDATE knowledge SET approved = 1 WHERE id = ?", (args.item_id,))
        db.commit()
        db.close()
        console.print(f"[green]✓ Approved: {args.item_id}[/green]")
    elif args.cmd == "reject":
        db = get_db()
        db.execute("UPDATE knowledge SET archived = 1 WHERE id = ?", (args.item_id,))
        db.commit()
        db.close()
        console.print(f"[red]✗ Archived: {args.item_id}[/red]")


if __name__ == "__main__":
    main()
