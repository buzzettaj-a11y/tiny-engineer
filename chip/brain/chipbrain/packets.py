"""CHP1 mic packets (PROTOCOL.md section 3) and stream bookkeeping."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

MAGIC = b"CHP1"
HEADER = struct.Struct("<4sIIHH")  # magic, seq, t_ms, flags, n
HEADER_SIZE = HEADER.size  # 16
SAMPLE_RATE = 22050

FLAG_MUTED = 1 << 0
FLAG_SPEAKER = 1 << 1
FLAG_MOVING = 1 << 2
FLAG_ASLEEP = 1 << 3


class PacketError(ValueError):
    pass


@dataclass(frozen=True)
class MicPacket:
    seq: int
    t_ms: int
    flags: int
    samples: np.ndarray  # int16, may be empty (heartbeat)

    @property
    def muted(self) -> bool:
        return bool(self.flags & FLAG_MUTED)

    @property
    def speaker(self) -> bool:
        return bool(self.flags & FLAG_SPEAKER)

    @property
    def moving(self) -> bool:
        return bool(self.flags & FLAG_MOVING)

    @property
    def asleep(self) -> bool:
        return bool(self.flags & FLAG_ASLEEP)

    @property
    def heartbeat(self) -> bool:
        return self.samples.size == 0


def parse_packet(data: bytes) -> MicPacket:
    if len(data) < HEADER_SIZE:
        raise PacketError("short packet")
    magic, seq, t_ms, flags, n = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise PacketError("bad magic")
    payload = data[HEADER_SIZE:]
    if len(payload) < n * 2:
        raise PacketError(f"truncated: header says {n} samples, got {len(payload) // 2}")
    samples = np.frombuffer(payload, dtype="<i2", count=n).astype(np.int16)
    return MicPacket(seq=seq, t_ms=t_ms, flags=flags, samples=samples)


def build_packet(seq: int, t_ms: int, flags: int, samples: np.ndarray | None) -> bytes:
    """Encode a packet (used by tests and the fake robot)."""
    s = np.zeros(0, dtype="<i2") if samples is None else np.asarray(samples, dtype="<i2")
    return HEADER.pack(MAGIC, seq & 0xFFFFFFFF, t_ms & 0xFFFFFFFF, flags & 0xFFFF, s.size) + s.tobytes()


@dataclass
class StreamStats:
    """Tracks sequence gaps and mic state from the packet flow."""

    packets: int = 0
    audio_packets: int = 0
    heartbeats: int = 0
    lost: int = 0  # packets missing according to seq
    reordered: int = 0  # late/duplicate packets dropped
    bad: int = 0
    speaker_dropped: int = 0
    last_seq: int | None = None
    last_flags: int = 0
    last_packet_at: float | None = None
    level: float = 0.0  # recent RMS 0..1
    sender: str | None = None
    restarts: int = 0
    _gap: int = field(default=0, repr=False)

    def accept(self, pkt: MicPacket, now: float) -> int | None:
        """Account for a packet. Returns the number of packets lost before it
        (0 normally), or None when the packet is stale and must be dropped."""
        self.packets += 1
        self.last_packet_at = now
        self.last_flags = pkt.flags
        gap = 0
        if self.last_seq is not None:
            delta = (pkt.seq - self.last_seq) & 0xFFFFFFFF
            if delta == 0 or delta > 0x80000000:
                # duplicate or older than the last one. A huge backward jump is
                # a robot reboot: resync instead of dropping forever.
                back = (self.last_seq - pkt.seq) & 0xFFFFFFFF
                if delta == 0 or back < 1000:
                    self.reordered += 1
                    return None
                self.restarts += 1
            elif delta > 1:
                if delta > 100000:
                    self.restarts += 1  # reboot that happened to land ahead
                else:
                    gap = delta - 1
                    self.lost += gap
        self.last_seq = pkt.seq
        if pkt.heartbeat:
            self.heartbeats += 1
        else:
            self.audio_packets += 1
            rms = float(np.sqrt(np.mean((pkt.samples.astype(np.float32) / 32768.0) ** 2)))
            self.level = 0.8 * self.level + 0.2 * rms
        return gap

    @property
    def muted(self) -> bool:
        return bool(self.last_flags & FLAG_MUTED)

    @property
    def asleep(self) -> bool:
        return bool(self.last_flags & FLAG_ASLEEP)

    @property
    def speaker(self) -> bool:
        return bool(self.last_flags & FLAG_SPEAKER)

    def streaming(self, now: float) -> bool:
        return self.last_packet_at is not None and now - self.last_packet_at < 2.5

    def as_dict(self, now: float) -> dict:
        return {
            "streaming": self.streaming(now),
            "muted": self.muted,
            "asleep": self.asleep,
            "speaker": self.speaker,
            "sender": self.sender,
            "packets": self.packets,
            "lost": self.lost,
            "reordered": self.reordered,
            "bad": self.bad,
            "speaker_dropped": self.speaker_dropped,
            "level": round(self.level, 4),
            "last_packet_age_s": None if self.last_packet_at is None else round(now - self.last_packet_at, 1),
        }
