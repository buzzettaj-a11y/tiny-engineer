"""Split streaming text into speakable sentences, and tidy text for TTS."""

from __future__ import annotations

import re

_ABBREV = {
    "mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "approx", "no", "fig", "jr", "sr",
}
_URL = re.compile(r"https?://\S+")
_MD = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)"), r"\1"),
    (re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)"), r"\1"),
    (re.compile(r"^\s*#+\s*", re.M), ""),
    (re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
]


def clean_for_speech(text: str) -> str:
    for rx, rep in _MD:
        text = rx.sub(rep, text)
    text = _URL.sub("a link", text)
    text = text.replace("&", " and ").replace("->", " to ").replace("=>", " to ")
    text = re.sub(r"[—–]", ", ", text)  # dashes read better as pauses
    text = re.sub(r"[*_#>|~]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceSplitter:
    """Feed text deltas, get complete sentences as soon as they end.

    A sentence ends at . ! ? (or a newline) followed by whitespace, except after
    common abbreviations, single initials and inside numbers like 3.5 or v1.2.
    Very long run-ons are cut at a comma so the first audio is not delayed.
    """

    def __init__(self, max_chars: int = 220, min_chars: int = 2):
        self.buf = ""
        self.max_chars = max_chars
        self.min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sent, self.buf = self.buf[:cut].strip(), self.buf[cut:].lstrip()
            if len(sent) >= self.min_chars:
                out.append(sent)
        return out

    def flush(self) -> list[str]:
        sent, self.buf = self.buf.strip(), ""
        return [sent] if len(sent) >= self.min_chars else []

    def _find_cut(self) -> int | None:
        b = self.buf
        for i, ch in enumerate(b):
            if ch == "\n" and b[:i].strip():
                return i + 1
            if ch in ".!?" and i + 1 < len(b):
                nxt = b[i + 1]
                if ch == "." and nxt.isdigit():
                    continue  # 3.5
                # swallow closing quotes/brackets and repeated punctuation
                j = i + 1
                while j < len(b) and b[j] in "\"')]!?.":
                    j += 1
                if j >= len(b):
                    return None  # need more text to decide
                if not b[j].isspace():
                    continue
                if ch == "." and self._is_abbrev(b[: i]):
                    continue
                return j
        if len(b) > self.max_chars:
            k = b.rfind(", ", 0, self.max_chars)
            if k > 40:
                return k + 2
        return None

    @staticmethod
    def _is_abbrev(before: str) -> bool:
        m = re.search(r"([A-Za-z.]+)$", before)
        if not m:
            return False
        word = m.group(1).lower().rstrip(".")
        if len(word) == 1 and word.isalpha():
            return True  # initials: "J. Smith"
        return word in _ABBREV
