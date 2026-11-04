"""Speech to text. faster-whisper is imported lazily so the rest of the brain
(and the tests) run without it."""

from __future__ import annotations

import logging
import threading

import numpy as np

from .config import SttCfg

log = logging.getLogger("chip.stt")


class Transcriber:
    """16 kHz float32 mono -> text. Called from a worker thread."""

    name = "none"

    def load(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> str:
        raise NotImplementedError


class WhisperTranscriber(Transcriber):
    name = "faster-whisper"

    def __init__(self, cfg: SttCfg):
        self.cfg = cfg
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel  # heavy, lazy

                log.info("loading Whisper %s (%s, %s)...", self.cfg.model, self.cfg.device, self.cfg.compute_type)
                self._model = WhisperModel(self.cfg.model, device=self.cfg.device, compute_type=self.cfg.compute_type)
                log.info("Whisper ready")

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> str:
        self.load()
        segments, _info = self._model.transcribe(
            np.asarray(audio, dtype=np.float32),
            language="en",
            beam_size=self.cfg.beam_size,
            initial_prompt=prompt if prompt is not None else self.cfg.initial_prompt,
            vad_filter=False,
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()


class NullTranscriber(Transcriber):
    """Used when faster-whisper is missing: the brain still runs hooks/focus."""

    name = "none"

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> str:
        return ""


def make_transcriber(cfg: SttCfg) -> Transcriber:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        log.warning("faster-whisper not installed: voice is OFF (pip install faster-whisper)")
        return NullTranscriber()
    return WhisperTranscriber(cfg)
