#!/usr/bin/env python3
"""Mojo companion intervention layer.

This module is intentionally small and local-first. It collects project
context, retrieves scoped Mojo knowledge, classifies whether an intervention
is warranted, emits a notification through an abstraction, and logs feedback.
It never edits human-authored instruction files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.panel import Panel

import advisory
import db_ops

console = Console()

INTERVENTION_TYPES = {
    "silent",
    "hard_warning",
    "soft_suggestion",
    "clarifying_question",
}

PROTECTED_INSTRUCTION_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "SKILL.md",
    "SKILLS.md",
}

RISK_TERMS = {
    "auto",
    "overwrite",
    "modify",
    "write",
    "inject",
    "sync",
    "promote",
    "global",
    "instruction",
    "production",
    "delete",
    "drop",
    "leak",
    "leakage",
    "forecast",
    "train",
    "훈련",
    "누수",
    "자동",
    "수정",
    "삭제",
    "운영",
}

APPROVAL_TERMS = {
    "explicit approval",
    "approved by user",
    "user approved",
    "manual approval",
    "승인",
    "명시적 승인",
}


@dataclass
class CompanionContext:
    project_path: str
    task: str = ""
    files: list[str] = field(default_factory=list)
    command: str = ""
    output: str = ""
    event: str = ""
    git_branch: str = ""
    git_diff: str = ""
    recent_files: list[str] = field(default_factory=list)
    mojo_md_excerpt: str = ""
    context_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "project_path": self.project_path,
            "task": self.task,
            "files": self.files,
            "command": self.command,
            "output": self.output,
            "event": self.event,
            "git_branch": self.git_branch,
            "git_diff": self.git_diff,
            "recent_files": self.recent_files,
            "mojo_md_excerpt": self.mojo_md_excerpt,
            "context_hash": self.context_hash,
        }


@dataclass
class Intervention:
    intervention_type: str = "silent"
    message: str = ""
    evidence_summary: str = ""
    recommended_action: str = ""
    source_knowledge_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    feedback_requested: bool = False
    event_id: str | None = None

    def to_event(self, context: CompanionContext, channel: str = "terminal") -> dict:
        return {
            "id": self.event_id,
            "timestamp": datetime.now().isoformat(),
            "project_path": context.project_path,
            "context_hash": context.context_hash,
            "knowledge_ids": self.source_knowledge_ids,
            "intervention_type": self.intervention_type,
            "message": self.message,
            "evidence_summary": self.evidence_summary,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "feedback_requested": int(self.feedback_requested),
            "context_snapshot": context.to_dict(),
            "notification_channel": channel,
        }

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "intervention_type": self.intervention_type,
            "message": self.message,
            "evidence_summary": self.evidence_summary,
            "recommended_action": self.recommended_action,
            "source_knowledge_ids": self.source_knowledge_ids,
            "confidence": self.confidence,
            "feedback_requested": self.feedback_requested,
        }


class NotificationEmitter:
    """Presentation abstraction for companion interventions."""

    def emit(self, intervention: Intervention) -> None:
        raise NotImplementedError


class TerminalNotificationEmitter(NotificationEmitter):
    def emit(self, intervention: Intervention) -> None:
        if intervention.intervention_type == "silent":
            return
        color = {
            "hard_warning": "red",
            "soft_suggestion": "cyan",
            "clarifying_question": "yellow",
        }.get(intervention.intervention_type, "blue")
        body = intervention.message
        if intervention.evidence_summary:
            body += f"\n\nEvidence: {intervention.evidence_summary}"
        if intervention.recommended_action:
            body += f"\n\nRecommended action: {intervention.recommended_action}"
        if intervention.event_id:
            body += f"\n\nEvent: {intervention.event_id}"
        console.print(Panel.fit(body, title=f"Mojo {intervention.intervention_type}", border_style=color))


class JsonNotificationEmitter(NotificationEmitter):
    def emit(self, intervention: Intervention) -> None:
        print(json.dumps(intervention.to_dict(), ensure_ascii=False))


def _run_git(project: Path, *args: str, max_chars: int = 12000) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout[:max_chars]


def _recent_files(project: Path, limit: int = 12) -> list[str]:
    files: list[tuple[float, str]] = []
    skip = {".git", ".mojo", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
    try:
        iterator = project.rglob("*")
    except OSError:
        return []
    for path in iterator:
        try:
            rel = path.relative_to(project)
        except ValueError:
            continue
        if any(part in skip for part in rel.parts) or not path.is_file():
            continue
        try:
            files.append((path.stat().st_mtime, rel.as_posix()))
        except OSError:
            continue
    files.sort(reverse=True)
    return [f for _, f in files[:limit]]


def _read_mojo_md(project: Path, max_chars: int = 6000) -> str:
    path = project / advisory.ADVISORY_FILE
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError:
        return ""


def _hash_context(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def collect_context(project_path: str | os.PathLike = ".",
                    task: str = "",
                    files: list[str] | None = None,
                    command: str = "",
                    output: str = "",
                    event: str = "") -> CompanionContext:
    project = Path(project_path).expanduser().resolve()
    current_files = files or []
    context = CompanionContext(
        project_path=str(project),
        task=task,
        files=current_files,
        command=command,
        output=output[:6000],
        event=event,
        git_branch=_run_git(project, "branch", "--show-current", max_chars=500).strip(),
        git_diff=_run_git(project, "diff", "--", max_chars=16000),
        recent_files=_recent_files(project),
        mojo_md_excerpt=_read_mojo_md(project),
    )
    context.context_hash = _hash_context(context.to_dict())
    return context


def retrieve_companion_knowledge(context: CompanionContext, limit: int = 8) -> list[dict]:
    task = "\n".join(
        p for p in (
            context.task,
            context.command,
            context.output,
            context.event,
            context.git_diff[:4000],
            context.mojo_md_excerpt[:2000],
        )
        if p
    )
    files = list(dict.fromkeys([*context.files, *context.recent_files]))
    return advisory.retrieve_advisory_context(
        context.project_path,
        task=task,
        file_paths=files,
        limit=limit,
    )


def _text_blob(context: CompanionContext) -> str:
    return "\n".join([
        context.task,
        " ".join(context.files),
        context.command,
        context.output,
        context.event,
        context.git_diff,
    ]).lower()


def _mentions_protected_instruction(text: str) -> bool:
    lower = text.lower()
    return any(name.lower() in lower for name in PROTECTED_INSTRUCTION_FILES)


def _has_approval_signal(text: str) -> bool:
    return any(term in text for term in APPROVAL_TERMS)


def _has_risk_signal(text: str) -> bool:
    return any(term in text for term in RISK_TERMS)


def _knowledge_summary(item: dict) -> str:
    lineage = item.get("source_lineage") or {}
    if isinstance(lineage, dict):
        source = f"{lineage.get('kind', 'source')}:{lineage.get('ref', '')}".rstrip(":")
    else:
        source = str(lineage)
    return (
        f"{item.get('id')} · scope={item.get('scope')} · "
        f"state={item.get('promotion_state')} · evidence={item.get('evidence_level')} · "
        f"source={source}"
    )


def _scope_compatible(item: dict, context: CompanionContext) -> bool:
    scope = item.get("scope", "project")
    if scope in {"universal", "domain", "workflow", "environment", "user_preference"}:
        return True
    if scope in {"project", "incident"}:
        project = item.get("project_path") or ""
        if not project:
            return False
        try:
            saved = Path(project).expanduser().resolve()
            current = Path(context.project_path).expanduser().resolve()
        except OSError:
            return False
        return saved == current or saved in current.parents or current in saved.parents
    return False


def _is_approved_for_warning(item: dict) -> bool:
    return (
        item.get("promotion_state") in {"project_approved", "generalized"}
        and bool(item.get("approved"))
        and item.get("evidence_level") != "raw_observation"
        and float(item.get("confidence") or 0.0) >= 0.7
    )


def _is_risky_taxon(item: dict) -> bool:
    return item.get("taxon") in {
        "anti_pattern",
        "leakage_warning",
        "environment_constraint",
        "evaluation_rule",
        "deployment_note",
    } or item.get("type") in {"anti_pattern", "debug_playbook"}


def classify_intervention(context: CompanionContext,
                          knowledge_items: list[dict],
                          sensitivity: str = "conservative") -> Intervention:
    """Rule-based intervention classifier.

    Default is intentionally quiet. Raw evidence never creates a hard warning
    by itself, and candidate knowledge is limited to suggestions/questions.
    """
    text = _text_blob(context)

    if _mentions_protected_instruction(text) and _has_risk_signal(text) and not _has_approval_signal(text):
        return Intervention(
            intervention_type="hard_warning",
            message=(
                "Warning: this appears to modify AGENTS.md, CLAUDE.md, SKILL.md, "
                "or SKILLS.md without explicit approval."
            ),
            evidence_summary="Protected human-authored instruction file mentioned with an automatic modification risk.",
            recommended_action=(
                "Keep Mojo output advisory, or ask for explicit approval before changing instruction files."
            ),
            confidence=0.95,
            feedback_requested=True,
        )

    compatible = [k for k in knowledge_items if _scope_compatible(k, context)]
    if not compatible:
        return Intervention()

    conflict = next((k for k in compatible if k.get("conflicts_with")), None)
    if conflict and conflict.get("promotion_state") != "raw":
        return Intervention(
            intervention_type="clarifying_question",
            message=(
                "Clarification needed: the relevant Mojo knowledge has conflicts or counterexamples."
            ),
            evidence_summary=_knowledge_summary(conflict),
            recommended_action="Inspect the conflicting knowledge before applying this guidance.",
            source_knowledge_ids=[conflict["id"], *list(conflict.get("conflicts_with") or [])],
            confidence=0.65,
            feedback_requested=True,
        )

    generalization_terms = {"generalize", "global", "universal", "promote", "instruction", "claude.md", "agents.md", "skills.md", "일반화", "전역", "승격"}
    if any(term in text for term in generalization_terms):
        scoped = next((
            k for k in compatible
            if k.get("scope") in {"project", "incident", "environment"}
            and not k.get("safe_to_generalize")
            and k.get("promotion_state") in {"candidate", "project_approved"}
        ), None)
        if scoped:
            return Intervention(
                intervention_type="clarifying_question",
                message=(
                    "Clarification needed: this knowledge appears scoped, but the current context discusses promotion or global instructions."
                ),
                evidence_summary=_knowledge_summary(scoped),
                recommended_action="Decide whether this should remain project-scoped or be explicitly reviewed for broader promotion.",
                source_knowledge_ids=[scoped["id"]],
                confidence=0.7,
                feedback_requested=True,
            )

    approved = [
        k for k in compatible
        if _is_approved_for_warning(k)
    ]
    risky = [
        k for k in approved
        if _is_risky_taxon(k) and (_has_risk_signal(text) or sensitivity != "conservative")
    ]
    if risky:
        top = max(risky, key=lambda k: float(k.get("confidence") or 0.0))
        return Intervention(
            intervention_type="hard_warning",
            message=(
                f"Warning: this direction may violate approved Mojo knowledge: {top.get('title')}."
            ),
            evidence_summary=_knowledge_summary(top),
            recommended_action=top.get("applies_when") or "Pause and verify the scoped constraint before continuing.",
            source_knowledge_ids=[top["id"]],
            confidence=min(0.95, max(0.72, float(top.get("confidence") or 0.72))),
            feedback_requested=True,
        )

    candidate = next((
        k for k in compatible
        if k.get("promotion_state") in {"candidate", "project_approved", "generalized"}
        and k.get("evidence_level") != "raw_observation"
        and float(k.get("confidence") or 0.0) >= 0.5
    ), None)
    if candidate:
        return Intervention(
            intervention_type="soft_suggestion",
            message=f"Suggestion: Mojo found relevant scoped knowledge: {candidate.get('title')}.",
            evidence_summary=_knowledge_summary(candidate),
            recommended_action=candidate.get("applies_when") or "Consider this guidance if it matches the current task.",
            source_knowledge_ids=[candidate["id"]],
            confidence=min(0.85, max(0.5, float(candidate.get("confidence") or 0.5))),
            feedback_requested=True,
        )

    return Intervention()


def log_intervention(context: CompanionContext,
                     intervention: Intervention,
                     channel: str = "terminal") -> Intervention:
    if intervention.intervention_type == "silent":
        return intervention
    db_ops.init_db()
    db = db_ops.get_db()
    try:
        event_id = db_ops.log_companion_intervention(
            db, intervention.to_event(context, channel=channel)
        )
    finally:
        db.close()
    intervention.event_id = event_id
    return intervention


def run_check(project_path: str | os.PathLike = ".",
              task: str = "",
              files: list[str] | None = None,
              command: str = "",
              output: str = "",
              event: str = "",
              sensitivity: str = "conservative",
              notify: bool = True,
              channel: str = "terminal",
              json_output: bool = False) -> Intervention:
    context = collect_context(
        project_path=project_path,
        task=task,
        files=files or [],
        command=command,
        output=output,
        event=event,
    )
    items = retrieve_companion_knowledge(context)
    intervention = classify_intervention(context, items, sensitivity=sensitivity)
    intervention = log_intervention(context, intervention, channel=channel)

    if notify and intervention.intervention_type != "silent":
        emitter: NotificationEmitter = JsonNotificationEmitter() if json_output else TerminalNotificationEmitter()
        emitter.emit(intervention)
    elif json_output:
        print(json.dumps(intervention.to_dict(), ensure_ascii=False))
    return intervention


def _state_path(project: Path) -> Path:
    return project / ".mojo" / "companion.json"


def _read_state(project: Path) -> dict:
    path = _state_path(project)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(project: Path, state: dict) -> None:
    path = _state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_companion(project_path: str | os.PathLike = ".",
                    interval: int = 30,
                    sensitivity: str = "conservative",
                    foreground: bool = False,
                    start_process: bool = True) -> dict:
    project = Path(project_path).expanduser().resolve()
    state = {
        "enabled": True,
        "project_path": str(project),
        "db_path": str(db_ops.DB_PATH),
        "sensitivity": sensitivity,
        "interval_seconds": interval,
        "notification_channels": ["terminal"],
        "started_at": datetime.now().isoformat(),
    }
    if foreground:
        _write_state(project, {**state, "pid": os.getpid(), "mode": "foreground"})
        _run_sidecar_loop(project, interval=interval, sensitivity=sensitivity)
        return state
    if start_process:
        log_path = project / ".mojo" / "companion.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(Path(__file__).resolve().parent / "mojo_cli.py"),
            "companion",
            "_run-sidecar",
            "--project",
            str(project),
            "--interval",
            str(interval),
            "--sensitivity",
            sensitivity,
        ]
        with log_path.open("ab") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        state["pid"] = proc.pid
        state["mode"] = "background"
        state["log_path"] = str(log_path)
    else:
        state["pid"] = None
        state["mode"] = "configured"
    _write_state(project, state)
    return state


def stop_companion(project_path: str | os.PathLike = ".") -> dict:
    project = Path(project_path).expanduser().resolve()
    state = _read_state(project)
    pid = state.get("pid")
    stopped = False
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except OSError:
            stopped = False
    state["enabled"] = False
    state["stopped_at"] = datetime.now().isoformat()
    state["stopped_process"] = stopped
    _write_state(project, state)
    return state


def companion_status(project_path: str | os.PathLike = ".") -> dict:
    project = Path(project_path).expanduser().resolve()
    state = _read_state(project)
    pid = state.get("pid")
    return {
        "project_path": str(project),
        "enabled": bool(state.get("enabled")),
        "pid": pid,
        "process_alive": _pid_alive(pid),
        "db_path": state.get("db_path", str(db_ops.DB_PATH)),
        "sensitivity": state.get("sensitivity", "conservative"),
        "notification_channels": state.get("notification_channels", ["terminal"]),
        "state_path": str(_state_path(project)),
    }


def _run_sidecar_loop(project: Path, interval: int, sensitivity: str) -> None:
    while True:
        state = _read_state(project)
        if state and not state.get("enabled", True):
            break
        run_check(project, sensitivity=sensitivity, notify=True)
        time.sleep(max(5, interval))


def _load_text_arg(value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.exists() and path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return value
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Mojo companion intervention layer")
    sub = parser.add_subparsers(dest="cmd")

    start_p = sub.add_parser("start", help="Start companion sidecar for a project")
    start_p.add_argument("--project", "-p", default=".")
    start_p.add_argument("--interval", type=int, default=30)
    start_p.add_argument("--sensitivity", choices=["conservative", "normal"], default="conservative")
    start_p.add_argument("--foreground", action="store_true")
    start_p.add_argument("--no-process", action="store_true", help="Write enabled sidecar state without spawning")

    stop_p = sub.add_parser("stop", help="Stop companion sidecar for a project")
    stop_p.add_argument("--project", "-p", default=".")

    status_p = sub.add_parser("status", help="Show companion status")
    status_p.add_argument("--project", "-p", default=".")

    check_p = sub.add_parser("check", help="Run one-shot intervention check")
    check_p.add_argument("--project", "-p", default=".")
    check_p.add_argument("--task", "-t", default="")
    check_p.add_argument("--file", "-f", action="append", default=[])
    check_p.add_argument("--command", default="")
    check_p.add_argument("--output", default="", help="Literal output or path to output file")
    check_p.add_argument("--event", default="", help="Literal event text or path to event file")
    check_p.add_argument("--sensitivity", choices=["conservative", "normal"], default="conservative")
    check_p.add_argument("--json", action="store_true")

    feedback_p = sub.add_parser("feedback", help="Record feedback for an intervention")
    feedback_p.add_argument("event_id")
    feedback_p.add_argument("feedback", choices=["useful", "not_useful", "too_noisy", "too_weak", "wrong"])
    feedback_p.add_argument("--accepted", action="store_true")
    feedback_p.add_argument("--dismissed", action="store_true")

    run_p = sub.add_parser("_run-sidecar", help=argparse.SUPPRESS)
    run_p.add_argument("--project", "-p", default=".")
    run_p.add_argument("--interval", type=int, default=30)
    run_p.add_argument("--sensitivity", choices=["conservative", "normal"], default="conservative")

    args = parser.parse_args()

    if args.cmd == "start":
        state = start_companion(
            args.project,
            interval=args.interval,
            sensitivity=args.sensitivity,
            foreground=args.foreground,
            start_process=not args.no_process,
        )
        console.print(Panel.fit(
            f"Enabled companion mode for {state['project_path']}\n"
            f"Mode: {state['mode']} · Sensitivity: {state['sensitivity']}\n"
            f"PID: {state.get('pid')}",
            title="Mojo Companion",
            border_style="green",
        ))
        return 0

    if args.cmd == "stop":
        state = stop_companion(args.project)
        console.print(f"[yellow]Companion disabled[/yellow] for {state.get('project_path')}")
        return 0

    if args.cmd == "status":
        status = companion_status(args.project)
        console.print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "check":
        intervention = run_check(
            args.project,
            task=args.task,
            files=args.file,
            command=args.command,
            output=_load_text_arg(args.output),
            event=_load_text_arg(args.event),
            sensitivity=args.sensitivity,
            json_output=args.json,
        )
        if not args.json and intervention.intervention_type == "silent":
            console.print("[dim]Mojo companion: silent[/dim]")
        return 0

    if args.cmd == "feedback":
        db_ops.init_db()
        db = db_ops.get_db()
        try:
            ok = db_ops.record_companion_feedback(
                db,
                args.event_id,
                args.feedback,
                accepted=args.accepted,
                dismissed=args.dismissed,
            )
        finally:
            db.close()
        if not ok:
            console.print(f"[red]Intervention not found:[/red] {args.event_id}")
            return 1
        console.print(f"[green]Recorded feedback[/green] {args.feedback} for {args.event_id}")
        return 0

    if args.cmd == "_run-sidecar":
        _run_sidecar_loop(
            Path(args.project).expanduser().resolve(),
            interval=args.interval,
            sensitivity=args.sensitivity,
        )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
