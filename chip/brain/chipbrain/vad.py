"""Voice activity detection and utterance segmentation (16 kHz float audio).

``Segmenter.feed()`` turns a continuous stream into events:

* ``SpeechStart``  - speech began (pre-roll included in later audio)
* ``Prefix``       - first ``prefix_s`` seconds of a segment, once, for the
                     early wake check
* ``Segment``      - a finished utterance (ended by ``end_silence_ms`` silence
                     or ``max_segment_s``)
* ``Noise``        - a blip too short to be speech; ``loud`` when it peaked
                     above ``loud_sound_dbfs`` (idle personality glances)

Times are stream seconds (samples / 16000), so tests are deterministic.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .audio import STT_RATE, dbfs
from .config import AudioCfg

log = logging.getLogger("chip.vad")

FRAME_MS = 30
FRAME = STT_RATE * FRAME_MS // 1000  # 480 samples


class EnergyVAD:
    """Adaptive noise floor + margin. Good enough in a quiet room; webrtcvad is
    used on top when installed."""

    def __init__(self, margin_db: float = 10.0, min_dbfs: float = -50.0):
        self.margin_db = margin_db
        self.min_dbfs = min_dbfs
        self.floor: float | None = None

    def is_speech(self, frame: np.ndarray) -> tuple[bool, float]:
        db = dbfs(frame)
        if self.floor is None:
            self.floor = min(db, -45.0)
        speech = db > max(self.floor + self.margin_db, self.min_dbfs)
        if not speech:
            # follow the floor down fast, up slowly
            a = 0.3 if db < self.floor else 0.03
            self.floor += a * (db - self.floor)
        else:
            self.floor += 0.002 * (db - self.floor)
        return speech, db


class WebRtcVAD(EnergyVAD):
    """webrtcvad decision AND a minimum energy (webrtcvad alone fires on hum)."""

    def __init__(self, aggressiveness: int, margin_db: float, min_dbfs: float):
        super().__init__(margin_db, min_dbfs)
        import webrtcvad  # lazy: optional dependency

        self._vad = webrtcvad.Vad(int(aggressiveness))

    def is_speech(self, frame: np.ndarray) -> tuple[bool, float]:
        floor_before = self.floor if self.floor is not None else -60.0
        energetic, db = super().is_speech(frame)
        pcm = np.clip(frame * 32767, -32768, 32767).astype("<i2").tobytes()
        try:
            voiced = self._vad.is_speech(pcm, STT_RATE)
        except Exception:  # pragma: no cover - defensive
            voiced = energetic
        # webrtcvad is good at "is this a voice", energy rules out hum / far noise
        return voiced and db > max(floor_before + self.margin_db / 2, self.min_dbfs), db


def make_vad(cfg: AudioCfg) -> EnergyVAD:
    if cfg.vad in ("auto", "webrtc"):
        try:
            v = WebRtcVAD(cfg.webrtc_aggressiveness, cfg.energy_margin_db, cfg.min_speech_dbfs)
            log.info("VAD: webrtcvad (aggressiveness %d)", cfg.webrtc_aggressiveness)
            return v
        except ImportError:
            if cfg.vad == "webrtc":
                log.warning("VAD: webrtcvad not installed, falling back to energy VAD")
    log.info("VAD: energy")
    return EnergyVAD(cfg.energy_margin_db, cfg.min_speech_dbfs)


@dataclass
class SpeechStart:
    t: float


@dataclass
class Prefix:
    audio: np.ndarray
    t_start: float


@dataclass
class Segment:
    audio: np.ndarray
    t_start: float
    t_end: float
    speech_ms: int
    peak_dbfs: float


@dataclass
class Noise:
    t: float
    duration_ms: int
    peak_dbfs: float
    loud: bool


@dataclass
class _Active:
    t_start: float
    frames: list = field(default_factory=list)
    voiced_frames: int = 0
    trailing_silence: int = 0
    peak: float = -120.0
    prefix_sent: bool = False


class Segmenter:
    START_FRAMES = 3  # consecutive voiced frames that start a segment

    def __init__(self, cfg: AudioCfg, vad: EnergyVAD | None = None):
        self.cfg = cfg
        self.vad = vad or EnergyVAD(cfg.energy_margin_db, cfg.min_speech_dbfs)
        self._buf = np.zeros(0, dtype=np.float32)
        self._t = 0.0  # stream time of the start of _buf
        self._preroll: deque = deque(maxlen=max(1, cfg.preroll_ms // FRAME_MS))
        self._run = 0  # consecutive voiced frames while idle
        self._active: _Active | None = None
        self.end_frames = max(1, cfg.end_silence_ms // FRAME_MS)
        self.max_frames = int(cfg.max_segment_s * 1000 / FRAME_MS)
        self.prefix_frames = int(cfg.prefix_s * 1000 / FRAME_MS)

    @property
    def in_speech(self) -> bool:
        return self._active is not None

    @property
    def now(self) -> float:
        return self._t

    def reset(self) -> None:
        """Drop any partial utterance (mute, robot speech)."""
        self._active = None
        self._run = 0
        self._preroll.clear()

    def feed(self, x: np.ndarray) -> list:
        events: list = []
        self._buf = np.concatenate([self._buf, np.asarray(x, dtype=np.float32)])
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            t = self._t
            self._t += FRAME / STT_RATE
            events.extend(self._frame(frame, t))
        return events

    def _frame(self, frame: np.ndarray, t: float) -> list:
        speech, db = self.vad.is_speech(frame)
        a = self._active
        if a is None:
            self._preroll.append((frame, speech, db))
            self._run = self._run + 1 if speech else 0
            if self._run < self.START_FRAMES:
                return []
            pre = list(self._preroll)
            start = t + FRAME / STT_RATE - len(pre) * FRAME / STT_RATE
            a = self._active = _Active(t_start=start)
            for f, s, d in pre:
                a.frames.append(f)
                a.voiced_frames += 1 if s else 0
                a.peak = max(a.peak, d)
            self._preroll.clear()
            self._run = 0
            return [SpeechStart(start)]

        a.frames.append(frame)
        a.peak = max(a.peak, db)
        if speech:
            a.voiced_frames += 1
            a.trailing_silence = 0
        else:
            a.trailing_silence += 1
        out: list = []
        # prefix = the first prefix_s of a segment that is STILL talking
        if (not a.prefix_sent and speech and len(a.frames) >= self.prefix_frames
                and a.voiced_frames * FRAME_MS >= self.cfg.min_speech_ms):
            a.prefix_sent = True
            out.append(Prefix(np.concatenate(a.frames), a.t_start))
        if a.trailing_silence >= self.end_frames or len(a.frames) >= self.max_frames:
            out.append(self._finish(t + FRAME / STT_RATE))
        return out

    def _finish(self, t_end: float):
        a = self._active
        self._active = None
        speech_ms = a.voiced_frames * FRAME_MS
        if speech_ms < self.cfg.min_speech_ms:
            dur = int(round((t_end - a.t_start) * 1000)) - a.trailing_silence * FRAME_MS
            return Noise(a.t_start, max(dur, FRAME_MS), a.peak, a.peak >= self.cfg.loud_sound_dbfs)
        # trim most of the trailing silence, keep ~150 ms
        keep_tail = max(0, a.trailing_silence - 5)
        frames = a.frames[: len(a.frames) - keep_tail] if keep_tail else a.frames
        return Segment(np.concatenate(frames), a.t_start, t_end, speech_ms, a.peak)
