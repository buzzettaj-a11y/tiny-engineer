"""Wake word "Chip".

Default path needs no trained model: Whisper transcribes the first ~1.2 s of
each utterance and ``match_wake`` looks for the name near the start. Risky
sound-alikes ("ship", "cheap", ...) only count right after a greeting, so
"ship it" or "cheap flights" never wake Chip.

Optional upgrade: an openWakeWord model (``OpenWakeWordDetector``) scores raw
audio frames and fires before any transcription.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np

from .config import WakeCfg

log = logging.getLogger("chip.wake")

_WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("-", " "))


@dataclass
class WakeMatch:
    matched: bool
    rest: str = ""  # what Joe said after the wake word (original casing)
    word: str = ""


def match_wake(text: str, cfg: WakeCfg) -> WakeMatch:
    """Find the wake word near the start of ``text``.

    Accepts "Chip, ...", "Hey Chip ...", "Okay, Chip ...", "Oh hey chip".
    """
    spans = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text.lower().replace("-", " "))]
    greetings = set(cfg.greetings)
    strong = set(cfg.words)
    weak = set(cfg.words_after_greeting)
    i = 0
    greeted = False
    # skip leading greetings ("oh hey", "okay")
    while i < len(spans) and spans[i][0] in greetings and i < 3:
        greeted = True
        i += 1
    for j in range(i, min(len(spans), i + max(1, cfg.max_position))):
        w = spans[j][0]
        if w in strong or (w in weak and greeted and j == i):
            rest = text[spans[j][2]:]
            rest = re.sub(r"^[\s,.!?;:\-]+", "", rest).strip()
            return WakeMatch(True, rest, w)
        # the only word allowed before the name (without a greeting) is a filler
        if w not in ("um", "uh", "so", "and", "well"):
            break
    return WakeMatch(False)


def mentions_wake(text: str, cfg: WakeCfg) -> bool:
    """Looser check for the 1.2 s prefix: Whisper may cut the last word."""
    return match_wake(text, cfg).matched


class WakeDetector:
    """Streaming wake detector over 16 kHz int16 audio. ``process`` returns True
    the moment the wake word is heard."""

    name = "none"

    def process(self, pcm16k: np.ndarray) -> bool:
        return False

    def reset(self) -> None:
        pass


class OpenWakeWordDetector(WakeDetector):
    """openWakeWord model (optional). Needs ``pip install openwakeword`` and a
    custom "chip" model (.onnx or .tflite)."""

    name = "openwakeword"
    CHUNK = 1280  # 80 ms at 16 kHz, what openWakeWord expects

    def __init__(self, model_path: str, threshold: float = 0.5):
        from openwakeword.model import Model  # lazy, optional

        framework = "tflite" if model_path.endswith(".tflite") else "onnx"
        self._model = Model(wakeword_models=[model_path], inference_framework=framework)
        self.threshold = threshold
        self._buf = np.zeros(0, dtype=np.int16)

    def process(self, pcm16k: np.ndarray) -> bool:
        self._buf = np.concatenate([self._buf, np.asarray(pcm16k, dtype=np.int16)])
        fired = False
        while len(self._buf) >= self.CHUNK:
            chunk, self._buf = self._buf[: self.CHUNK], self._buf[self.CHUNK:]
            scores = self._model.predict(chunk)
            if scores and max(scores.values()) >= self.threshold:
                fired = True
        return fired

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.int16)
        try:
            self._model.reset()
        except Exception:
            pass


def make_detector(cfg: WakeCfg, base_resolve=None) -> WakeDetector | None:
    """openWakeWord detector when configured and installed, else None (Whisper
    prefix path)."""
    if not cfg.openwakeword_model:
        return None
    path = str(base_resolve(cfg.openwakeword_model)) if base_resolve else cfg.openwakeword_model
    try:
        det = OpenWakeWordDetector(path, cfg.openwakeword_threshold)
        log.info("wake: openWakeWord model %s", path)
        return det
    except Exception as e:  # ImportError or bad model
        log.warning("wake: openWakeWord unavailable (%s); using Whisper prefix check", e)
        return None
