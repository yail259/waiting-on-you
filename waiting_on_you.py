"""How long did your AI agents wait on you? Reads Claude Code and Codex session logs, read-only.

A wait is the time from the agent's last message in a turn to the next prompt you type in that
session: the agent has stopped and nothing moves until you answer. Waits under 20 seconds are you
replying in flow and are not counted; waits over 8 hours are sleep or a parked project and are
reported apart. Subagent turns are skipped.

Each wait is labelled by how the agent stopped: it asked you something (its last words end in a
question mark) or it finished and waited for your next instruction.

Nothing leaves your machine. Only timestamps and whether the agent's last words ended in a question
mark are used; no message text is printed or stored.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

__version__ = "0.1.0"

FLOW_SECONDS = 20
PARKED_SECONDS = 8 * 3600
ASKED = "asked you a question"
FINISHED = "finished, waiting for your next step"

# Text the harness injects as a "user" message; not something you typed.
CLAUDE_HARNESS = ("<system-reminder", "<task-notification", "<local-command", "<command-", "Caveat:", "<cross-session")
CODEX_HARNESS = ("<environment_context", "<user_instructions", "<turn_aborted", "<user_action")


@dataclass
class Wait:
    start: datetime
    seconds: float
    kind: str
    source: str
    session: str


@dataclass
class Report:
    since: datetime
    until: datetime
    waits: list[Wait] = field(default_factory=list)
    parked: int = 0
    prompts: int = 0
    sessions: set[str] = field(default_factory=set)
    sources: dict[str, int] = field(default_factory=dict)


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def ends_with_question(text: str) -> bool:
    return text.rstrip(" \n*_`)\"'").endswith("?")


def read_lines(path: str):
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                yield json.loads(line)
            except ValueError:
                continue


# ---- Claude Code: ~/.claude/projects/<project>/<session>.jsonl ----

def claude_text(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def claude_typed(entry: dict) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") == "tool_result" for part in content):
        return False
    text = claude_text(entry).strip()
    return bool(text) and not text.startswith(CLAUDE_HARNESS)


def claude_events(path: str):
    """Yields (who, time, last words, id): 'agent' turns and the prompts 'you' typed.

    Claude Code can write the same message into a log more than once (a long session that was
    compacted or resumed replays its history), so each event carries the message's own id."""
    for entry in read_lines(path):
        at = parse_time(entry.get("timestamp"))
        if not at:
            continue
        key = entry.get("uuid")
        if entry.get("type") == "assistant" and not entry.get("isSidechain"):
            yield "agent", at, claude_text(entry).strip(), key
        elif claude_typed(entry):
            yield "you", at, "", key


# ---- Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (beta) ----

def codex_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def codex_events(path: str):
    for entry in read_lines(path):
        at = parse_time(entry.get("timestamp"))
        payload = entry.get("payload") or {}
        if not at or not isinstance(payload, dict):
            continue
        kind = entry.get("type")
        if kind == "event_msg" and payload.get("type") == "task_complete":
            yield "agent", at, (payload.get("last_agent_message") or "").strip(), None
        elif kind == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
            yield "agent", at, codex_text(payload).strip(), None
        elif kind == "event_msg" and payload.get("type") == "user_message":
            text = (payload.get("message") or "").strip()
            if text and not text.startswith(CODEX_HARNESS):
                yield "you", at, "", None
        elif kind == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
            text = codex_text(payload).strip()
            if text and not text.startswith(CODEX_HARNESS):
                yield "you", at, "", None


SOURCES = {
    "claude": (claude_events, ("~/.claude/projects", "*", "*.jsonl")),
    "codex": (codex_events, ("~/.codex/sessions", "**", "rollout-*.jsonl")),
}


def find_logs(source: str, root: str | None) -> list[str]:
    base, *pattern = SOURCES[source][1]
    roots = [root] if root else [os.path.expanduser(base)]
    # Windows logs, read from WSL.
    if not root and os.path.isdir("/mnt/c/Users"):
        tail = base.removeprefix("~/")
        roots += glob.glob(os.path.join("/mnt/c/Users", "*", tail))
    paths: list[str] = []
    for directory in roots:
        paths += glob.glob(os.path.join(directory, *pattern), recursive=True)
    return sorted(set(paths))


def collect(report: Report, source: str, paths: list[str]) -> None:
    events_of = SOURCES[source][0]
    # The same message can be logged more than once, in one file or in a resumed session's copy:
    # count each once (by its id, or its time where the log has no ids) and read them in time order.
    seen: set = set()
    for path in paths:
        # A log last written before the window opened has nothing in it.
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < report.since:
                continue
        except OSError:
            continue
        events = []
        for who, at, words, key in events_of(path):
            identity = (who, key or at)
            if identity in seen:
                continue
            seen.add(identity)
            events.append((at, who, words))
        events.sort(key=lambda event: event[0])
        last_at, last_words = None, ""
        for at, who, words in events:
            if not (report.since <= at < report.until):
                last_at = None
                continue
            if who == "agent":
                last_at = at
                if words:
                    last_words = words
                report.sessions.add(path)
                continue
            report.prompts += 1
            if last_at:
                gap = (at - last_at).total_seconds()
                if gap > PARKED_SECONDS:
                    report.parked += 1
                elif gap >= FLOW_SECONDS:
                    kind = ASKED if ends_with_question(last_words) else FINISHED
                    report.waits.append(Wait(last_at, gap, kind, source, path))
                    report.sources[source] = report.sources.get(source, 0) + 1
            last_at, last_words = None, ""


def overlap(waits: list[Wait]) -> tuple[float, int]:
    """Wall-clock hours with at least one agent waiting, and the most waiting at once."""
    edges = sorted([(w.start, 1) for w in waits] + [(w.start + timedelta(seconds=w.seconds), -1) for w in waits])
    covered = 0.0
    waiting = peak = 0
    previous = None
    for at, step in edges:
        if previous is not None and waiting > 0:
            covered += (at - previous).total_seconds()
        waiting += step
        peak = max(peak, waiting)
        previous = at
    return covered / 3600, peak


def summarise(report: Report, tz: timezone) -> dict:
    waits = sorted(report.waits, key=lambda w: w.seconds)
    total = sum(w.seconds for w in waits) / 3600
    wall, peak = overlap(report.waits)
    by_kind: dict[str, dict] = {}
    for wait in waits:
        group = by_kind.setdefault(wait.kind, {"count": 0, "hours": 0.0})
        group["count"] += 1
        group["hours"] += wait.seconds / 3600
    per_day: dict[str, float] = {}
    for wait in report.waits:
        day = wait.start.astimezone(tz).date().isoformat()
        per_day[day] = per_day.get(day, 0.0) + wait.seconds / 3600
    return {
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "sessions": len(report.sessions),
        "prompts": report.prompts,
        "waits": len(waits),
        "hours_waiting": round(total, 1),
        "wall_clock_hours_with_someone_waiting": round(wall, 1),
        "most_waiting_at_once": peak,
        "median_wait_minutes": round(waits[len(waits) // 2].seconds / 60, 1) if waits else 0,
        "by_kind": {kind: {"count": v["count"], "hours": round(v["hours"], 1)} for kind, v in sorted(by_kind.items())},
        "per_day_hours": {day: round(hours, 1) for day, hours in sorted(per_day.items())},
        "parked_over_8h": report.parked,
        "by_source": report.sources,
    }


def render(summary: dict) -> str:
    if not summary["waits"]:
        return "No waits found in this window. Either you reply fast, or there are no logs here (try --claude-dir / --codex-dir)."
    hours = summary["hours_waiting"]
    lines = [
        f"Your agents waited on you for {hours:g} hours.",
        "",
        f"  {summary['waits']} waits across {summary['sessions']} sessions, median {summary['median_wait_minutes']:g} min.",
        f"  At the worst moment, {summary['most_waiting_at_once']} sessions were waiting on you at once.",
        f"  For {summary['wall_clock_hours_with_someone_waiting']:g} hours of the clock, at least one was.",
        "",
    ]
    for kind, group in summary["by_kind"].items():
        lines.append(f"  {kind}: {group['count']} times, {group['hours']:g} h")
    lines.append("")
    widest = max(summary["per_day_hours"].values())
    for day, value in summary["per_day_hours"].items():
        bar = "█" * max(1, round(24 * value / widest)) if value else ""
        lines.append(f"  {day}  {bar} {value:g} h")
    lines += [
        "",
        f"  Not counted: replies within {FLOW_SECONDS} s (you were in flow), and {summary['parked_over_8h']} gaps over 8 h (sleep, parked work).",
        f"  Window: {summary['since'][:16]} to {summary['until'][:16]}.",
    ]
    return "\n".join(lines)


def share_line(summary: dict) -> str:
    return (
        f"Last week my AI agents waited on me for {summary['hours_waiting']:g} hours "
        f"({summary['waits']} waits, up to {summary['most_waiting_at_once']} at once). "
        "Measured with waiting-on-you."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waiting-on-you", description="How long did your AI agents wait on you?")
    parser.add_argument("--days", type=float, default=7, help="look back this many days (default 7)")
    parser.add_argument("--since", help="start date, YYYY-MM-DD, in your time zone")
    parser.add_argument("--until", help="end date, YYYY-MM-DD (exclusive), in your time zone")
    parser.add_argument("--utc-offset", type=float, help="your time zone as hours from UTC (default: this machine's)")
    parser.add_argument("--claude-dir", help="Claude Code projects folder (default ~/.claude/projects)")
    parser.add_argument("--codex-dir", help="Codex sessions folder (default ~/.codex/sessions)")
    parser.add_argument("--only", choices=sorted(SOURCES), help="read just one agent's logs")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    parser.add_argument("--share", action="store_true", help="print one line you can paste")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.utc_offset is not None:
        tz = timezone(timedelta(hours=args.utc_offset))
    else:
        tz = datetime.now().astimezone().tzinfo or timezone.utc
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=tz)
        until = datetime.fromisoformat(args.until).replace(tzinfo=tz) if args.until else datetime.now(tz)
    else:
        until = datetime.now(tz)
        since = until - timedelta(days=args.days)

    report = Report(since=since, until=until)
    roots = {"claude": args.claude_dir, "codex": args.codex_dir}
    for source in [args.only] if args.only else SOURCES:
        collect(report, source, find_logs(source, roots[source]))

    summary = summarise(report, tz)
    if args.json:
        print(json.dumps(summary, indent=2))
    elif args.share:
        print(share_line(summary))
    else:
        print(render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
