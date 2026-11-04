import struct

import numpy as np
import pytest

from chipbrain.audio import (StreamResampler, concat_wavs, decode_wav, encode_wav, float_to_int16, to_robot_wav,
                             wav_duration_s)
from chipbrain.packets import (FLAG_MUTED, FLAG_SPEAKER, HEADER_SIZE, PacketError, StreamStats, build_packet,
                               parse_packet)
from fakes import validate_wav


def test_packet_roundtrip_and_layout():
    s = np.arange(256, dtype=np.int16) - 128
    data = build_packet(7, 123456, FLAG_SPEAKER, s)
    assert data[:4] == b"CHP1"
    assert len(data) == HEADER_SIZE + 512
    assert struct.unpack_from("<IIHH", data, 4) == (7, 123456, FLAG_SPEAKER, 256)
    p = parse_packet(data)
    assert p.seq == 7 and p.t_ms == 123456 and p.speaker and not p.muted
    assert np.array_equal(p.samples, s)


def test_heartbeat_and_errors():
    hb = parse_packet(build_packet(1, 0, FLAG_MUTED, None))
    assert hb.heartbeat and hb.muted
    with pytest.raises(PacketError):
        parse_packet(b"XXXX" + bytes(12))
    with pytest.raises(PacketError):
        parse_packet(b"CHP1")
    bad = bytearray(build_packet(1, 0, 0, np.zeros(256, np.int16)))
    with pytest.raises(PacketError):
        parse_packet(bytes(bad[:-10]))


def _pkt(seq, flags=0, n=256):
    return parse_packet(build_packet(seq, seq * 11, flags, np.zeros(n, np.int16) if n else None))


def test_stats_gaps_duplicates_wrap():
    st = StreamStats()
    assert st.accept(_pkt(10), 0.0) == 0
    assert st.accept(_pkt(11), 0.01) == 0
    assert st.accept(_pkt(15), 0.02) == 3  # 12,13,14 lost
    assert st.lost == 3
    assert st.accept(_pkt(13), 0.03) is None  # late
    assert st.accept(_pkt(15), 0.03) is None  # duplicate
    assert st.reordered == 2
    st2 = StreamStats()
    st2.accept(_pkt(0xFFFFFFFE), 0)
    assert st2.accept(_pkt(0xFFFFFFFF), 0) == 0
    assert st2.accept(_pkt(0), 0) == 0  # wraps
    assert st2.accept(_pkt(2), 0) == 1


def test_stats_muted_heartbeat_and_reboot():
    st = StreamStats()
    st.accept(_pkt(5000), 0)
    assert st.accept(_pkt(5001, FLAG_MUTED, n=0), 1.0) == 0
    assert st.muted and st.heartbeats == 1
    # robot rebooted: seq restarts near 0 -> resync, not "late"
    assert st.accept(_pkt(0), 2.0) == 0
    assert st.restarts == 1
    assert st.streaming(3.0) and not st.streaming(10.0)


def test_resampler_frequency_length_and_streaming():
    rate_in, rate_out = 22050, 16000
    t = np.arange(rate_in) / rate_in
    x = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    one = StreamResampler(rate_in, rate_out).process(x)
    rs = StreamResampler(rate_in, rate_out)
    parts = [rs.process(x[i:i + 256]) for i in range(0, len(x), 256)]
    streamed = np.concatenate(parts)
    assert abs(len(streamed) - rate_out) <= 2
    assert abs(len(one) - len(streamed)) <= 1
    n = min(len(one), len(streamed))
    assert np.max(np.abs(one[:n] - streamed[:n])) < 1e-4  # block edges are seamless
    spec = np.abs(np.fft.rfft(streamed[1000:15000]))
    peak_hz = np.argmax(spec) * rate_out / 14000
    assert abs(peak_hz - 1000) < 5


def test_resampler_attenuates_alias():
    rate_in = 22050
    t = np.arange(rate_in) / rate_in
    x = np.sin(2 * np.pi * 10000 * t)  # above the 8 kHz output Nyquist
    y = StreamResampler(rate_in, 16000).process(x)
    assert np.sqrt(np.mean(y[200:] ** 2)) < 0.05


def test_wav_encode_matches_play_contract():
    wav = encode_wav(np.zeros(22050, np.int16))
    ok, err, dur = validate_wav(wav)
    assert ok, err
    assert dur == pytest.approx(1.0)
    assert wav[:4] == b"RIFF" and len(wav) == 44 + 44100


def test_to_robot_wav_converts_rate_channels_width():
    # 16 kHz stereo 32-bit int -> 22050 mono 16-bit
    import io
    import wave

    t = np.arange(16000) / 16000
    mono = (0.5 * np.sin(2 * np.pi * 440 * t) * 2**31).astype("<i4")
    stereo = np.stack([mono, mono], axis=1).reshape(-1)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)
        w.setframerate(16000)
        w.writeframes(stereo.tobytes())
    out = to_robot_wav(bio.getvalue())
    ok, err, dur = validate_wav(out)
    assert ok, err
    assert dur == pytest.approx(1.0, abs=0.01)
    x, rate = decode_wav(out)
    assert rate == 22050 and 0.45 < np.max(np.abs(x)) < 0.55


def test_concat_wavs():
    a = encode_wav(float_to_int16(np.full(2205, 0.1)))
    b = encode_wav(float_to_int16(np.full(4410, 0.1)))
    c = concat_wavs([a, b], gap_ms=100)
    assert validate_wav(c)[0]
    assert wav_duration_s(c) == pytest.approx(0.1 + 0.1 + 0.2, abs=0.001)
