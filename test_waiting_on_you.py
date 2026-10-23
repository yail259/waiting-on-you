import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import waiting_on_you as w

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def claude(kind: str, minutes: float, text: str = "", **extra) -> dict:
    role = "assistant" if kind == "agent" else "user"
    return {"type": role, "timestamp": at(minutes), "message": {"role": role, "content": [{"type": "text", "text": text}]}, **extra}


def codex_done(minutes: float, text: str) -> dict:
    return {"type": "event_msg", "timestamp": at(minutes), "payload": {"type": "task_complete", "last_agent_message": text}}


def codex_you(minutes: float, text: str) -> dict:
    return {"type": "event_msg", "timestamp": at(minutes), "payload": {"type": "user_message", "message": text}}


class Logs:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()

    def __del__(self):
        self.dir.cleanup()

    def write(self, name: str, entries: list[dict]) -> str:
        path = os.path.join(self.dir.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        return path


def run(source: str, paths: list[str]) -> dict:
    report = w.Report(since=T0 - timedelta(days=1), until=T0 + timedelta(days=1))
    w.collect(report, source, paths)
    return w.summarise(report, timezone.utc)


class ClaudeTest(unittest.TestCase):
    def setUp(self):
        self.logs = Logs()

    def test_counts_waits_and_labels_questions(self):
        path = self.logs.write("p/a.jsonl", [
            claude("you", 0, "Build the thing"),
            claude("agent", 1, "Done. Want me to ship it?"),
            claude("you", 31, "Yes"),
            claude("agent", 32, "Shipped."),
            claude("you", 92, "Next task"),
        ])
        summary = run("claude", [path])
        self.assertEqual(summary["waits"], 2)
        self.assertEqual(summary["by_kind"][w.ASKED], {"count": 1, "hours": 0.5})
        self.assertEqual(summary["by_kind"][w.FINISHED], {"count": 1, "hours": 1.0})
        self.assertEqual(summary["prompts"], 3)

    def test_flow_replies_and_parked_gaps_are_not_waits(self):
        path = self.logs.write("p/b.jsonl", [
            claude("agent", 0, "Ok"),
            claude("you", 0.2, "quick reply"),
            claude("agent", 1, "Ok"),
            claude("you", 1 + 9 * 60, "next morning"),
        ])
        summary = run("claude", [path])
        self.assertEqual(summary["waits"], 0)
        self.assertEqual(summary["parked_over_8h"], 1)

    def test_harness_messages_tool_results_and_subagents_are_skipped(self):
        path = self.logs.write("p/c.jsonl", [
            claude("agent", 0, "Running"),
            claude("you", 5, "<system-reminder>noise</system-reminder>"),
            {"type": "user", "timestamp": at(6), "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            claude("agent", 7, "Subagent says hi", isSidechain=True),
            claude("you", 30, "Real prompt"),
        ])
        summary = run("claude", [path])
        self.assertEqual(summary["waits"], 1)
        self.assertAlmostEqual(summary["hours_waiting"], 0.5)

    def test_parallel_sessions_overlap(self):
        a = self.logs.write("p/d.jsonl", [claude("agent", 0, "Done"), claude("you", 60, "go")])
        b = self.logs.write("p/e.jsonl", [claude("agent", 30, "Done"), claude("you", 90, "go")])
        summary = run("claude", [a, b])
        self.assertEqual(summary["hours_waiting"], 2.0)
        self.assertEqual(summary["wall_clock_hours_with_someone_waiting"], 1.5)
        self.assertEqual(summary["most_waiting_at_once"], 2)

    def test_a_replayed_history_in_the_same_file_counts_once(self):
        first = [claude("agent", 0, "Done", uuid="a1"), claude("you", 60, "go", uuid="u1")]
        later = [claude("agent", 61, "On it", uuid="a2"), claude("you", 121, "next", uuid="u2")]
        path = self.logs.write("p/h.jsonl", first + later + first + later)
        summary = run("claude", [path])
        self.assertEqual(summary["waits"], 2)
        self.assertEqual(summary["hours_waiting"], 2.0)

    def test_a_duplicated_session_file_counts_once(self):
        entries = [claude("agent", 0, "Done"), claude("you", 60, "go")]
        a = self.logs.write("p/f.jsonl", entries)
        b = self.logs.write("q/f.jsonl", entries)
        self.assertEqual(run("claude", [a, b])["waits"], 1)


class CodexTest(unittest.TestCase):
    def test_task_complete_to_next_user_message(self):
        logs = Logs()
        path = logs.write("2026/10/20/rollout-x.jsonl", [
            codex_you(0, "<environment_context>cwd</environment_context>"),
            codex_you(0.1, "Fix the bug"),
            codex_done(2, "Fixed. Should I also add a test?"),
            codex_you(32, "yes"),
        ])
        summary = run("codex", [path])
        self.assertEqual(summary["waits"], 1)
        self.assertEqual(summary["by_kind"], {w.ASKED: {"count": 1, "hours": 0.5}})


class OutputTest(unittest.TestCase):
    def test_render_and_share_never_include_message_text(self):
        logs = Logs()
        path = logs.write("p/g.jsonl", [claude("agent", 0, "SECRET PLAN?"), claude("you", 45, "SECRET REPLY")])
        summary = run("claude", [path])
        for text in (w.render(summary), w.share_line(summary), json.dumps(summary)):
            self.assertNotIn("SECRET", text)
        self.assertIn("0.8 hours", w.render(summary))


if __name__ == "__main__":
    unittest.main()
