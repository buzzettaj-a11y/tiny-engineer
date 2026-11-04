"""Claude Code sessions seen through hook events.

Sessions are keyed by (host, session_id) and labelled "<cwd folder> on <host>".
"""

from __future__ import annotations

import difflib
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Iterable

from .animmap import animation_for_event

# Events that mean "Claude is doing something in this session right now".
NOT_ACTIVITY = {"SessionEnd", "Notification"}
WAITING_EVENTS = {"Stop", "PermissionRequest", "Notification"}

_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def _short(v, n: int = 80) -> str:
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _basename(p: str) -> str:
    p = (p or "").rstrip("/\\")
    return re.split(r"[/\\]", p)[-1] if p else ""


def tool_summary(tool: str | None, tool_input) -> str:
    """'Edit app.py', 'Bash npm test', 'Grep TODO'..."""
    tool = tool or "?"
    ti = tool_input if isinstance(tool_input, dict) else {}
    if not ti and isinstance(tool_input, str):
        return f"{tool} {_short(tool_input, 50)}"
    for key in ("file_path", "notebook_path", "path"):
        if isinstance(ti.get(key), str) and ti[key]:
            return f"{tool} {_basename(ti[key])}"
    for key in ("command", "pattern", "query", "url", "description", "prompt"):
        if isinstance(ti.get(key), str) and ti[key]:
            return f"{tool} {_short(ti[key], 50)}"
    return tool


def normalize_name(s: str) -> str:
    """'Job search on VM two' -> 'jobsearchonvm2' for fuzzy session lookup."""
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return "".join(_NUM_WORDS.get(t, t) for t in toks)


@dataclass
class Session:
    host: str
    session_id: str
    cwd: str = ""
    first_seen: float = 0.0
    last_event_at: float = 0.0
    last_activity_at: float = 0.0
    last_event: str = ""
    status: str = "started"
    waiting: bool = False  # finished or needs Joe
    ended: bool = False
    ended_at: float | None = None
    task_started_at: float | None = None
    last_task_s: float | None = None
    last_anim: str | None = None
    events: int = 0
    prompts: deque = field(default_factory=lambda: deque(maxlen=4))
    tools: deque = field(default_factory=lambda: deque(maxlen=8))
    files: OrderedDict = field(default_factory=OrderedDict)
    errors: deque = field(default_factory=lambda: deque(maxlen=4))
    last_message: str = ""
    label_suffix: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.host, self.session_id)

    @property
    def folder(self) -> str:
        return _basename(self.cwd) or "unknown folder"

    @property
    def label(self) -> str:
        return f"{self.folder}{self.label_suffix} on {self.host}"

    @property
    def id(self) -> str:
        return f"{self.host}/{self.session_id}"

    def idle_s(self, now: float) -> float:
        return now - (self.last_activity_at or self.first_seen)

    def apply(self, ev: dict, now: float) -> None:
        name = ev.get("hook_event_name", "")
        self.events += 1
        self.last_event = name
        self.last_event_at = now
        if ev.get("cwd"):
            self.cwd = str(ev["cwd"])
        if name not in NOT_ACTIVITY:
            self.last_activity_at = now
        anim = animation_for_event(ev)
        if anim:
            self.last_anim = anim
        tool = ev.get("tool_name")
        if name == "SessionStart":
            self.ended, self.ended_at = False, None
            self.status = "just started" if ev.get("source") == "startup" else f"resumed ({ev.get('source', '?')})"
            self.waiting = False
        elif name == "UserPromptSubmit":
            p = _short(ev.get("prompt", ""), 160)
            if p:
                self.prompts.append(p)
            self.task_started_at = now
            self.status = "working"
            self.waiting = False
        elif name == "PreToolUse":
            s = tool_summary(tool, ev.get("tool_input"))
            self.tools.append(s)
            self.status = f"running {s}"
            self.waiting = False
            ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
            if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                f = ti.get("file_path") or ti.get("notebook_path")
                if isinstance(f, str) and f:
                    self.files.pop(f, None)
                    self.files[f] = now
                    while len(self.files) > 8:
                        self.files.popitem(last=False)
        elif name == "PostToolUseFailure":
            err = ev.get("error") or ev.get("tool_response") or ""
            self.errors.append(f"{tool_summary(tool, ev.get('tool_input'))} failed: {_short(err, 120)}")
            self.status = f"{tool or 'a tool'} just failed"
        elif name == "PostToolBatch":
            self.status = "thinking"
        elif name == "SubagentStart":
            self.status = "running a subagent"
        elif name == "PreCompact":
            self.status = "compacting its context"
        elif name == "PermissionRequest":
            self.status = f"waiting for permission ({tool_summary(tool, ev.get('tool_input'))})"
            self.waiting = True
        elif name == "PermissionDenied":
            self.status = f"permission denied for {tool or 'a tool'}"
        elif name == "Notification":
            self.last_message = _short(ev.get("message", ""), 160)
            self.status = f"needs you: {self.last_message}" if self.last_message else "needs you"
            self.waiting = True
        elif name == "Stop":
            if self.task_started_at is not None:
                self.last_task_s = now - self.task_started_at
                self.task_started_at = None
            self.status = "finished, waiting for Joe"
            self.waiting = True
        elif name == "StopFailure":
            self.errors.append(_short(ev.get("error") or ev.get("message") or "stopped with an error", 120))
            self.status = "stopped with an error"
            self.waiting = True
            self.task_started_at = None
        elif name == "SessionEnd":
            self.ended, self.ended_at = True, now
            self.status = f"ended ({ev.get('reason', 'exit')})"
            self.waiting = False

    def brief(self, now: float) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "host": self.host,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "status": self.status,
            "waiting": self.waiting,
            "ended": self.ended,
            "idle_s": round(self.idle_s(now)),
            "last_event": self.last_event,
            "last_anim": self.last_anim,
        }

    def describe(self, now: float, detail: bool) -> str:
        idle = self.idle_s(now)
        ago = "just now" if idle < 10 else f"{_ago(idle)} ago"
        line = f"{self.label}: {self.status} (last activity {ago})"
        if self.task_started_at is not None:
            line += f"; current task running {_ago(now - self.task_started_at)}"
        elif self.last_task_s:
            line += f"; last task took {_ago(self.last_task_s)}"
        if not detail:
            return line
        parts = [line]
        if self.prompts:
            parts.append("  Recent prompts: " + " | ".join(f'"{p}"' for p in self.prompts))
        if self.tools:
            parts.append("  Recent tools: " + ", ".join(self.tools))
        if self.files:
            parts.append("  Files edited: " + ", ".join(_basename(f) for f in self.files))
        if self.errors:
            parts.append("  Errors: " + " | ".join(self.errors))
        if self.last_message:
            parts.append(f'  Last notification: "{self.last_message}"')
        return "\n".join(parts)


def _ago(s: float) -> str:
    s = max(0, int(s))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)}m"
    return f"{s / 3600:.1f}h"


class SessionStore:
    def __init__(self, forget_ended_after_s: float = 1800, forget_idle_after_s: float = 43200):
        self.sessions: dict[tuple[str, str], Session] = {}
        self.forget_ended_after_s = forget_ended_after_s
        self.forget_idle_after_s = forget_idle_after_s

    def ingest(self, ev: dict, now: float) -> Session:
        host = _short(str(ev.get("host") or "unknown"), 40)
        sid = str(ev.get("session_id") or "unknown")
        key = (host, sid)
        s = self.sessions.get(key)
        if s is None:
            s = Session(host=host, session_id=sid, cwd=str(ev.get("cwd") or ""), first_seen=now, last_activity_at=now)
            self.sessions[key] = s
        s.apply(ev, now)
        self._relabel()
        return s

    def _relabel(self) -> None:
        groups: dict[str, list[Session]] = {}
        for s in self.sessions.values():
            s.label_suffix = ""
            groups.setdefault(f"{s.folder} on {s.host}".lower(), []).append(s)
        for group in groups.values():
            if len(group) > 1:
                group.sort(key=lambda x: x.first_seen)
                for i, s in enumerate(group[1:], start=2):
                    s.label_suffix = f" #{i}"

    def prune(self, now: float) -> list[Session]:
        gone = [
            s for s in self.sessions.values()
            if (s.ended and s.ended_at is not None and now - s.ended_at > self.forget_ended_after_s)
            or now - s.last_event_at > self.forget_idle_after_s
        ]
        for s in gone:
            del self.sessions[s.key]
        if gone:
            self._relabel()
        return gone

    def all(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.last_activity_at, reverse=True)

    def live(self) -> list[Session]:
        return [s for s in self.all() if not s.ended]

    def most_recent_active(self) -> Session | None:
        live = self.live()
        return live[0] if live else None

    def get(self, sid: str) -> Session | None:
        for s in self.sessions.values():
            if sid in (s.id, s.session_id):
                return s
        return None

    def find(self, query: str) -> Session | None:
        """Session by id, label, folder or host, tolerant of speech ("job search
        on VM two"). Prefers live, recently active sessions."""
        if not query:
            return None
        exact = self.get(query)
        if exact:
            return exact
        q = normalize_name(query)
        if not q:
            return None
        cands = self.live() + [s for s in self.all() if s.ended]
        best, best_score = None, 0.0
        for s in cands:
            label, folder, host = normalize_name(s.label), normalize_name(s.folder), normalize_name(s.host)
            if q in (label, folder):
                return s
            score = 0.0
            if folder and (folder in q or q in folder):
                score = 0.9 if host and host in q else 0.8
            elif host and q == host:
                score = 0.7
            else:
                score = max(difflib.SequenceMatcher(None, q, label).ratio(),
                            difflib.SequenceMatcher(None, q, folder).ratio() * 0.95)
            if score > best_score:
                best, best_score = s, score
        return best if best_score >= 0.6 else None

    def summary(self, now: float, focused: Session | None, limit: int = 6) -> str:
        lines: list[str] = []
        if focused:
            lines.append("FOCUSED: " + focused.describe(now, detail=True))
        others = [s for s in self.all() if s is not focused][:limit]
        for s in others:
            lines.append("other: " + s.describe(now, detail=False))
        return "\n".join(lines) if lines else "No Claude Code sessions reported yet."

    def labels(self) -> Iterable[str]:
        return [s.label for s in self.all()]
