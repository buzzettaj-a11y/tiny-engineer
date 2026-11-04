"""Conversation window: when speech without the wake word is accepted."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

NEVER = float("-inf")


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str


class Conversation:
    """States: idle -> listening (woken) -> thinking -> speaking -> follow-up
    window (``follow_up_s``) -> idle. In the follow-up window and while
    listening, Joe can talk without saying "Chip"."""

    def __init__(self, follow_up_s: float = 8.0, history_turns: int = 6):
        self.follow_up_s = follow_up_s
        self.state = "idle"
        self.follow_up_until = NEVER
        self.history: deque[Turn] = deque(maxlen=max(2, history_turns * 2))
        self.last_heard = ""
        self.last_reply = ""

    def accepts_without_wake(self, now: float) -> bool:
        # while Chip is thinking/answering, Joe adding something counts too
        return self.state != "idle" or now <= self.follow_up_until

    def active(self, now: float) -> bool:
        """In a conversation: idle personality and hook animations hold off."""
        return self.state != "idle" or now <= self.follow_up_until

    def woke(self, now: float) -> None:
        """Wake word only ("Chip?"): listen for the follow-up."""
        self.state = "listening"
        self.follow_up_until = now + self.follow_up_s

    def start_reply(self) -> None:
        self.state = "thinking"

    def speaking(self) -> None:
        self.state = "speaking"

    def reply_done(self, now: float) -> None:
        self.state = "idle"
        self.follow_up_until = now + self.follow_up_s

    def expire(self, now: float) -> bool:
        """True exactly once when the conversation ends."""
        if self.state == "listening" and now > self.follow_up_until:
            self.state = "idle"
            self.follow_up_until = NEVER
            return True
        if self.state == "idle" and self.follow_up_until != NEVER and now > self.follow_up_until:
            self.follow_up_until = NEVER
            return True
        return False

    def add(self, role: str, text: str) -> None:
        if text:
            self.history.append(Turn(role, text))

    def messages(self) -> list[dict]:
        """History as Anthropic messages (must alternate, start with user)."""
        msgs: list[dict] = []
        for t in self.history:
            if msgs and msgs[-1]["role"] == t.role:
                msgs[-1]["content"] += " " + t.text
            else:
                msgs.append({"role": t.role, "content": t.text})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        return msgs

    def as_dict(self, now: float) -> dict:
        return {
            "state": self.state,
            "active": self.active(now),
            "follow_up_s_left": max(0.0, round(self.follow_up_until - now, 1)),
            "turns": len(self.history),
        }
