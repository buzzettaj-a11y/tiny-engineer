"""Which session Chip watches, and when to tell Joe about the others.

Auto mode follows the most recently active session, but only leaves the
current one when it has been idle for ``auto_switch_idle_s`` (default 2 min)
or has ended, and every switch is announced. Explicit focus (voice, tool or
POST /focus) sticks until Joe goes back to auto or that session ends.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .config import FocusCfg
from .sessions import Session, SessionStore

log = logging.getLogger("chip.focus")


def spoken(label: str) -> str:
    """'Job_Search on VM-2' -> 'Job Search on VM 2' (TTS reads dashes oddly)."""
    return re.sub(r"\s+", " ", re.sub(r"[-_./\\]+", " ", label)).strip()


@dataclass
class FocusChange:
    session: Session | None
    previous: Session | None
    reason: str  # "auto" | "explicit" | "ended"
    announce: str | None


class FocusManager:
    def __init__(self, store: SessionStore, cfg: FocusCfg):
        self.store = store
        self.cfg = cfg
        self.mode = "auto"
        self._key: tuple[str, str] | None = None

    @property
    def focused(self) -> Session | None:
        return self.store.sessions.get(self._key) if self._key else None

    def _switch(self, new: Session | None, reason: str, announce: str | None) -> FocusChange:
        prev = self.focused
        self._key = new.key if new else None
        log.info("focus → %s (%s)", new.label if new else "nobody", reason)
        return FocusChange(new, prev, reason, announce)

    def set_focus(self, query: str | None, now: float) -> tuple[bool, str, FocusChange | None]:
        """Explicit focus. ``None``/"auto" returns to auto mode."""
        if query is None or str(query).strip().lower() in ("", "auto", "none", "null"):
            self.mode = "auto"
            change = self.evaluate(now, announce=False)
            cur = self.focused
            msg = "Auto focus on" + (f", watching {cur.label}." if cur else ".")
            return True, msg, change
        s = self.store.find(str(query))
        if s is None:
            known = ", ".join(self.store.labels()) or "none yet"
            return False, f"No session matches '{query}'. Known: {known}.", None
        self.mode = "explicit"
        if s is self.focused:
            return True, f"Already watching {s.label}.", None
        return True, f"Watching {s.label}.", self._switch(s, "explicit", None)

    def on_event(self, session: Session, ev: dict, now: float) -> FocusChange | None:
        if (
            self.mode == "explicit"
            and session is self.focused
            and ev.get("hook_event_name") == "SessionEnd"
            and ev.get("reason") not in ("clear", "resume")
        ):
            self.mode = "auto"
            log.info("focused session ended; back to auto focus")
        return self.evaluate(now)

    def evaluate(self, now: float, announce: bool = True) -> FocusChange | None:
        if self.mode != "auto":
            return None
        cur = self.focused
        cand = self.store.most_recent_active()
        if cand is None or cand is cur:
            if cur is None and self._key is not None:
                self._key = None  # pruned
            return None
        if cur is None:
            return self._switch(cand, "auto", f"Watching {spoken(cand.label)}." if announce else None)
        if cur.ended or cur.idle_s(now) >= self.cfg.auto_switch_idle_s:
            if cand.last_activity_at <= cur.last_activity_at and not cur.ended:
                return None
            return self._switch(cand, "auto", f"Switching to {spoken(cand.label)}." if announce else None)
        return None

    def as_dict(self) -> dict:
        f = self.focused
        return {"mode": self.mode, "session": f.id if f else None, "label": f.label if f else None}


class HeadsUp:
    """One short spoken line when a session Joe is NOT watching needs him."""

    LINES = {
        "Stop": "Heads up, {label} is waiting for you.",
        "PermissionRequest": "Heads up, {label} needs a permission.",
        "Notification": "Heads up, {label} needs you.",
    }

    def __init__(self, cfg: FocusCfg):
        self.cfg = cfg
        self._last_session: dict[tuple[str, str], float] = {}
        self._last_any: float | None = None

    def check(self, session: Session, ev: dict, focused: Session | None, now: float) -> str | None:
        if not self.cfg.heads_up:
            return None
        name = ev.get("hook_event_name")
        if name not in self.LINES or session is focused or session.ended:
            return None
        last = self._last_session.get(session.key)
        if last is not None and now - last < self.cfg.heads_up_session_interval_s:
            return None
        if self._last_any is not None and now - self._last_any < self.cfg.heads_up_global_interval_s:
            return None
        self._last_session[session.key] = now
        self._last_any = now
        return self.LINES[name].format(label=spoken(session.label))
