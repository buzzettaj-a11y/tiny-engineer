"""Audio helpers: streaming resampler, WAV encode/decode, levels.

Pure numpy so it runs anywhere; no scipy needed.
"""

from __future__ import annotations

import io
import math
import struct
import wave
import numpy as np

ROBOT_RATE = 22050
STT_RATE = 16000


def _lowpass_taps(cutoff_ratio: float, taps: int = 63) -> np.ndarray:
    """Windowed-sinc low-pass. cutoff_ratio = cutoff / input_rate (0..0.5)."""
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff_ratio * np.sinc(2 * cutoff_ratio * n)
    h *= np.blackman(taps)
    return (h / h.sum()).astype(np.float64)


class StreamResampler:
    """Block-by-block resampler with carried state (no clicks at block edges).

    Anti-alias FIR (when downsampling) followed by linear interpolation, which is
    plenty for speech recognition and TTS clips.
    """

    def __init__(self, in_rate: int, out_rate: int, taps: int = 63):
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.step = in_rate / out_rate  # input samples per output sample
        self._pos = 0.0  # next output position, in units of input samples from seq[0]
        self._fir = None
        if out_rate < in_rate:
            self._fir = _lowpass_taps(0.45 * out_rate / in_rate, taps)
            self._fir_tail = np.zeros(taps - 1, dtype=np.float64)
        self._prev: float | None = None  # last (filtered) input sample of the previous block

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self._fir is not None:
            buf = np.concatenate([self._fir_tail, x])
            x = np.convolve(buf, self._fir, mode="valid")  # constant group delay, fine
            self._fir_tail = buf[-(len(self._fir) - 1):]
        # seq[0] is the previous block's last sample, so interpolation spans edges
        seq = x if self._prev is None else np.concatenate([[self._prev], x])
        last = len(seq) - 1
        out = np.zeros(0)
        pos = self._pos
        if pos <= last:
            k = int(math.floor((last - pos) / self.step)) + 1
            p = pos + self.step * np.arange(k)
            i0 = np.floor(p).astype(np.int64)
            frac = p - i0
            i1 = np.minimum(i0 + 1, last)
            out = seq[i0] * (1 - frac) + seq[i1] * frac
            pos = pos + self.step * k
        self._pos = pos - last  # re-base on this block's last sample
        self._prev = float(seq[-1])
        return out.astype(np.float32)


def resample_oneshot(x: np.ndarray, in_rate: int, out_rate: int) -> np.ndarray:
    if in_rate == out_rate:
        return np.asarray(x, dtype=np.float32)
    return StreamResampler(in_rate, out_rate).process(x)


def int16_to_float(x: np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float32) / 32768.0


def float_to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(np.asarray(x, dtype=np.float64) * 32767.0), -32768, 32767).astype(np.int16)


def dbfs(x: np.ndarray) -> float:
    """RMS level in dBFS of float samples (-1..1)."""
    if x.size == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(np.square(x, dtype=np.float64))))
    return 20 * math.log10(max(rms, 1e-6))


def encode_wav(pcm: np.ndarray, rate: int = ROBOT_RATE) -> bytes:
    """16-bit mono PCM WAV with the canonical 44-byte header."""
    data = np.asarray(pcm, dtype="<i2").tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
        b"data", len(data),
    )
    return header + data


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Any PCM WAV (8/16/24/32-bit int, or 32-bit float) -> (mono float32, rate)."""
    with io.BytesIO(data) as bio:
        try:
            with wave.open(bio, "rb") as w:
                ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
                raw = w.readframes(frames)
                fmt = 1
        except wave.Error:
            return _decode_wav_float(data)
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width {width} (format {fmt})")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x.astype(np.float32), rate


def _decode_wav_float(data: bytes) -> tuple[np.ndarray, int]:
    """Minimal RIFF walker for IEEE float WAVs (wave module refuses them)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", body, 0)
        elif cid == b"data" and fmt:
            tag, ch, rate, _, _, bits = fmt
            if tag == 3 and bits == 32:
                x = np.frombuffer(body[: len(body) // 4 * 4], dtype="<f4")
            elif tag == 3 and bits == 64:
                x = np.frombuffer(body[: len(body) // 8 * 8], dtype="<f8").astype(np.float32)
            else:
                raise ValueError(f"unsupported WAV format tag {tag}/{bits}")
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            return x.astype(np.float32), rate
        pos += 8 + size + (size & 1)
    raise ValueError("no audio data")


def to_robot_wav(data: bytes, gain: float = 1.0) -> bytes:
    """Convert any WAV to what POST /play accepts: 16-bit mono 22050 Hz."""
    x, rate = decode_wav(data)
    if rate != ROBOT_RATE:
        x = resample_oneshot(x, rate, ROBOT_RATE)
    if gain != 1.0:
        x = x * gain
    return encode_wav(float_to_int16(x))


def wav_pcm(data: bytes) -> np.ndarray:
    """int16 PCM of a robot-format WAV (16-bit mono)."""
    x, _ = decode_wav(data)
    return float_to_int16(x)


def concat_wavs(clips: list[bytes], gap_ms: int = 120) -> bytes:
    gap = np.zeros(int(ROBOT_RATE * gap_ms / 1000), dtype=np.int16)
    parts: list[np.ndarray] = []
    for i, c in enumerate(clips):
        if i:
            parts.append(gap)
        parts.append(wav_pcm(c))
    return encode_wav(np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16))


def wav_duration_s(data: bytes) -> float:
    x, rate = decode_wav(data)
    return len(x) / float(rate)


def tone_wav(seconds: float = 0.3, freq: float = 440.0, rate: int = ROBOT_RATE) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return encode_wav(float_to_int16(0.3 * np.sin(2 * np.pi * freq * t)), rate)
