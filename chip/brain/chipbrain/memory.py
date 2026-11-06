"""Chip's memory: what Joe has taught it ("Chip, remember ...").

Kept in a small JSON file next to the config so it survives restarts, and put
in front of every model (local or Claude) on every turn. See ADR 0003.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("chip.memory")


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


# Words that say nothing about WHICH fact is meant ("forget that Joe likes coffee").
FILLER = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "at", "for", "with", "about", "that", "this",
    "it", "its", "is", "are", "be", "was", "i", "me", "my", "you", "your", "he", "him", "his", "we",
    "like", "likes", "liked", "want", "wants", "thing", "things", "stuff", "remember", "forget", "please",
    "always", "never", "from", "now", "should",
}


class Memory:
    def __init__(self, path: Path, max_items: int = 50, owner: str = "Joe"):
        self.path = Path(path)
        self.max_items = max_items
        self.owner = owner
        self.facts: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            self.facts = [f for f in data.get("facts", []) if isinstance(f, dict) and f.get("fact")]
        except FileNotFoundError:
            self.facts = []
        except (OSError, ValueError) as e:
            log.warning("memory file %s unreadable (%s); starting empty", self.path, e)
            self.facts = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"facts": self.facts}, indent=2), "utf-8")
        tmp.replace(self.path)

    def remember(self, fact: str) -> str:
        fact = " ".join(str(fact).split())
        if not fact:
            return "Nothing to remember."
        key = _norm(fact)
        if any(_norm(f["fact"]) == key for f in self.facts):
            return "I already remember that."
        if len(self.facts) >= self.max_items:
            return (f"My memory is full ({self.max_items} things). "
                    f"Ask {self.owner} what to forget first.")
        self.facts.append({"fact": fact, "added": time.strftime("%Y-%m-%d")})
        self._save()
        log.info("remembered: %s", fact)
        return f"Remembered: {fact}"

    def forget(self, about: str) -> str:
        """Forget the facts that contain every meaningful word of ``about``."""
        filler = FILLER | {self.owner.lower()}
        words = {w for w in _norm(about).split() if w not in filler}
        if not words:
            return "Nothing I remember matches that. Say which thing to forget."
        keep, gone = [], []
        for f in self.facts:
            (gone if words <= set(_norm(f["fact"]).split()) else keep).append(f)
        if not gone:
            return "Nothing I remember matches that."
        self.facts = keep
        self._save()
        log.info("forgot: %s", "; ".join(f["fact"] for f in gone))
        return "Forgot: " + "; ".join(f["fact"] for f in gone)

    def prompt(self) -> str:
        if not self.facts:
            return ""
        return f"Things {self.owner} has taught you (follow these):\n" + "\n".join(f"- {f['fact']}" for f in self.facts)
