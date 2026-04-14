#!/usr/bin/env python3
"""Knowledge base statistics and ROI dashboard."""

import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).parent))

from db_ops import get_db, get_stats

console = Console()


def show_stats(show_roi: bool = False):
    """Display knowledge base statistics."""
    db = get_db()
    stats = get_stats(db)

    # Pipeline-stage counts from raw_sessions give users a clearer
    # picture when `Total Knowledge Items: 0`. Without this, a user
    # who ran `scan sessions` + hooks but never paid for `extract`
    # sees an empty store and assumes mojo is broken.
    pending_row = db.execute(
        "SELECT COUNT(*) FROM raw_sessions WHERE extracted = 0"
    ).fetchone()
    extracted_row = db.execute(
        "SELECT COUNT(*) FROM raw_sessions WHERE extracted = 1"
    ).fetchone()
    pending_sessions = pending_row[0] if pending_row else 0
    extracted_sessions = extracted_row[0] if extracted_row else 0

    mojo_home_env = os.environ.get("MOJO_HOME", "")
    store_label = mojo_home_env or str(Path.home() / ".mojo")

    # Main stats
    console.print(Panel.fit(
        f"[dim]Store: {store_label}[/dim]\n"
        f"[bold]Total Knowledge Items:[/bold] {stats['total_knowledge']}\n"
        f"[bold]Sessions — pending / extracted:[/bold] "
        f"{pending_sessions} / {extracted_sessions}\n"
        f"[bold]Total API Cost:[/bold] ${stats['total_extraction_cost_usd']:.4f}\n"
        f"[bold]Total Reuses:[/bold] {stats['total_usage_count']}",
        title="Mojo Stats",
        border_style="blue",
    ))

    # Actionable hint for the "empty store" case — the single most
    # common point of confusion for first-time users.
    if stats["total_knowledge"] == 0:
        if pending_sessions > 0:
            console.print(
                f"[yellow]Store is empty, but {pending_sessions} session(s) "
                f"are registered and waiting for extraction.[/yellow]\n"
                "[dim]Run [cyan]mojo extract[/cyan] (needs ANTHROPIC_API_KEY) "
                "or [cyan]mojo import-seed seeds/seed_knowledge.json[/cyan].[/dim]"
            )
        else:
            console.print(
                "[yellow]Store is empty and no sessions are registered.[/yellow]\n"
                "[dim]Try:[/dim]\n"
                "[dim]  • [cyan]mojo scan git <repo>[/cyan]  (free, rule-based)[/dim]\n"
                "[dim]  • [cyan]mojo scan sessions[/cyan]    "
                "(register past Claude Code sessions for this project)[/dim]\n"
                "[dim]  • [cyan]mojo import-seed seeds/seed_knowledge.json[/cyan]  "
                "(hand-curated, grade B+)[/dim]"
            )

    # By type
    if stats["by_type"]:
        table = Table(title="By Type")
        table.add_column("Type")
        table.add_column("Count", justify="right")
        for t, c in sorted(stats["by_type"].items()):
            table.add_row(t, str(c))
        console.print(table)

    # By domain
    if stats["by_domain"]:
        table = Table(title="By Domain")
        table.add_column("Domain")
        table.add_column("Count", justify="right")
        for d, c in sorted(stats["by_domain"].items(), key=lambda x: -x[1]):
            table.add_row(d, str(c))
        console.print(table)

    # ROI estimates
    if show_roi:
        show_roi_dashboard(db, stats)

    # Knowledge gap analysis (Practical Insight)
    show_gap_analysis(db)

    db.close()


def show_roi_dashboard(db, stats):
    """Estimate ROI: cost vs value of extracted knowledge."""
    total_cost = stats["total_extraction_cost_usd"]
    total_reuses = stats["total_usage_count"]

    # Estimate time saved: each reuse saves ~5 min of context re-explanation
    minutes_saved = total_reuses * 5

    console.print(Panel.fit(
        f"[bold]Extraction Cost:[/bold] ${total_cost:.4f}\n"
        f"[bold]Knowledge Reuses:[/bold] {total_reuses}\n"
        f"[bold]Est. Time Saved:[/bold] {minutes_saved} min "
        f"({minutes_saved / 60:.1f} hrs)",
        title="Cost & Impact",
        border_style="green",
    ))


def show_gap_analysis(db):
    """Identify knowledge gaps (Practical Insight)."""
    # Check for domains without anti_patterns
    domains = db.execute(
        "SELECT DISTINCT domain FROM knowledge WHERE archived = 0"
    ).fetchall()

    gaps = []
    for row in domains:
        domain = row[0]
        anti_count = db.execute(
            "SELECT COUNT(*) FROM knowledge "
            "WHERE domain = ? AND type = 'anti_pattern' AND archived = 0",
            (domain,)
        ).fetchone()[0]

        if anti_count == 0:
            total = db.execute(
                "SELECT COUNT(*) FROM knowledge "
                "WHERE domain = ? AND archived = 0", (domain,)
            ).fetchone()[0]
            if total >= 3:  # Only flag if domain has meaningful content
                gaps.append(domain)

    if gaps:
        console.print()
        console.print(Panel.fit(
            "[bold]Knowledge Gaps Detected:[/bold]\n" +
            "\n".join(
                f"  • [yellow]{d}[/yellow]: no anti_patterns recorded. "
                f"Failure cases could prevent repeated mistakes."
                for d in gaps
            ),
            title="Gap Analysis",
            border_style="yellow",
        ))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mojo statistics")
    parser.add_argument("--roi", action="store_true", help="Show ROI dashboard")
    args = parser.parse_args()

    show_stats(show_roi=args.roi)


if __name__ == "__main__":
    main()
