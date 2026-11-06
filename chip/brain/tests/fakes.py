"""Test doubles: a fake robot HTTP server that enforces the PROTOCOL.md
contract, and fake STT / TTS / Claude backends."""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import numpy as np

from chipbrain.audio import encode_wav, float_to_int16
from chipbrain.claude import LLMBackend
from chipbrain.stt import Transcriber
from chipbrain.tts import TTSBackend

ANIMS = {"none", "typing", "reading", "thinking", "talking", "ring", "welcome", "attention", "error", "abort",
         "dead", "wakeup", "sleep", "listening"}


def validate_wav(body: bytes) -> tuple[bool, str, float]:
    """Check what POST /play requires: RIFF/WAVE, 16-bit mono PCM 22050 Hz."""
    if len(body) < 44 or body[:4] != b"RIFF" or body[8:12] != b"WAVE":
        return False, "not a WAV file", 0
    pos, fmt, data_len = 12, None, None
    while pos + 8 <= len(body):
        cid, size = body[pos:pos + 4], struct.unpack_from("<I", body, pos + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", body, pos + 8)
        elif cid == b"data":
            data_len = size
            if pos + 8 + size > len(body):
                return False, "truncated data", 0
            break
        pos += 8 + size + (size & 1)
    if data_len is None:
        return False, "no audio data", 0
    tag, ch, rate, byte_rate, align, bits = fmt
    if (tag, ch, rate, bits) != (1, 1, 22050, 16) or byte_rate != 44100 or align != 2:
        return False, "expected 16-bit mono PCM at 22050 Hz", 0
    if struct.unpack_from("<I", body, 4)[0] != len(body) - 8:
        return False, "bad RIFF size", 0
    return True, "", data_len / 44100.0


class FakeRobot:
    """Threading server that answers 503 busy when two requests overlap, like
    the single-threaded robot would make clients wait. ``overlaps`` counts
    serialization violations."""

    def __init__(self, token: str = "", play_speed: float = 0.02, sensor: bool = True):
        self.token = token
        self.play_speed = play_speed  # seconds slept per second of audio
        self.requests: list[dict] = []
        self.overlaps = 0
        self.active = 0
        self.lock = threading.Lock()
        self.animation = "none"
        self.busy_next = 0  # answer this many requests with 503 busy first
        self.muted = False
        self.mute_switch = False  # firmware switch mode: POST /mic refused (ADR 0004)
        self.settings: dict = {}
        self.sensor = sensor
        self.saved_mins = [20, 30, 10, 100, 40]
        self.saved_maxs = [140, 150, 80, 170, 140]
        self.hard_mins = [0, 0, 0, 0, 0]
        self.hard_maxs = [180, 180, 180, 180, 180]
        self.soft_mins = list(self.saved_mins)
        self.soft_maxs = list(self.saved_maxs)
        self.bumps: list[dict] = []
        self.explore_safe: dict = {}  # (joint, dir) -> safe deg
        self.explore_error: tuple | None = None  # (status, error message) to refuse explore with
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeRobot":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def paths(self, prefix: str = "") -> list[str]:
        return [r["path"] for r in self.requests if r["path"].startswith(prefix)]

    def anims(self) -> list[str]:
        return [parse_qs(urlsplit(r["path"]).query)["name"][0] for r in self.requests
                if r["method"] == "POST" and r["path"].startswith("/anim")]

    def plays(self) -> list[dict]:
        return [r for r in self.requests if r["path"].startswith("/play")]

    def ranges_json(self) -> dict:
        return {"ok": True, "saved_mins": self.saved_mins, "saved_maxs": self.saved_maxs,
                "soft_mins": self.soft_mins, "soft_maxs": self.soft_maxs,
                "hard_mins": self.hard_mins, "hard_maxs": self.hard_maxs,
                "current_ma": 120.0, "sensor": self.sensor, "bump_threshold_ma": 650, "bumps": self.bumps}

    def _handler(self):
        robot = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _reply(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def _do(self):
                with robot.lock:
                    robot.active += 1
                    overlap = robot.active > 1
                    if overlap:
                        robot.overlaps += 1
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                rec = {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body,
                       "t": time.monotonic(), "has_length": self.headers.get("Content-Length") is not None}
                try:
                    if overlap:
                        return self._reply(503, {"ok": False, "error": "busy"})
                    if robot.busy_next > 0:
                        robot.busy_next -= 1
                        rec["busy"] = True
                        robot.requests.append(rec)
                        return self._reply(503, {"ok": False, "error": "busy"})
                    robot.requests.append(rec)
                    if robot.token and self.headers.get("Authorization") != f"Bearer {robot.token}":
                        return self._reply(401, {"ok": False, "error": "unauthorized"})
                    status, obj = robot.route(self.command, self.path, body, rec)
                    return self._reply(status, obj)
                finally:
                    with robot.lock:
                        robot.active -= 1

            do_GET = do_POST = _do

        return H

    def route(self, method: str, path: str, body: bytes, rec: dict):
        u = urlsplit(path)
        p = u.path
        if p == "/anim":
            if method == "GET":
                return 200, {"ok": True, "animation": self.animation}
            name = parse_qs(u.query).get("name", [None])[0]
            if not name:
                return 400, {"ok": False, "error": "missing name"}
            if name not in ANIMS:
                return 400, {"ok": False, "error": "unknown animation"}
            self.animation = name
            return 200, {"ok": True, "animation": name}
        if p == "/play" or p.startswith("/play/"):
            pose = p[6:] or "talking"
            if pose not in ("talking", "typing", "reading", "thinking", "none", "listening"):
                return 400, {"ok": False, "error": "name must be talking, typing, reading, thinking or none"}
            if not rec["has_length"]:
                return 411, {"ok": False, "error": "Content-Length required"}
            ok, err, dur = validate_wav(body)
            rec["wav_ok"], rec["duration"] = ok, dur
            if not ok:
                return 415, {"ok": False, "error": err}
            time.sleep(dur * self.play_speed)
            return 200, {"ok": True, "played_ms": round(dur * 1000)}
        data = json.loads(body) if body else None
        if p == "/pose":
            pose = (data or {}).get("pose")
            ms = (data or {}).get("ms")
            if not (isinstance(pose, list) and len(pose) == 5 and all(v is None or -1 <= v <= 1 for v in pose)):
                return 400, {"ok": False, "error": "bad pose"}
            if not (isinstance(ms, int) and 100 <= ms <= 5000):
                return 400, {"ok": False, "error": "bad ms"}
            self.animation = "none"
            return 200, {"ok": True}
        if p == "/mic":
            if method == "POST":
                if self.mute_switch:
                    return 409, {"ok": False, "error": "mute follows the switch"}
                self.muted = bool(data["muted"])
            return 200, {"ok": True, "muted": self.muted, "streaming": not self.muted, "present": True, "level": 0.01,
                         "mute_input": "switch" if self.mute_switch else "button"}
        if p == "/health":
            return 200, {"ok": True, "version": "fake", "mic": {"present": True, "muted": self.muted,
                                                                "streaming": True}, "current_ma": 100.0, "bumps": 0}
        if p == "/settings" and method == "POST":
            self.settings.update(data)
            return 200, {"ok": True}
        if p == "/ranges":
            if method == "GET":
                return 200, self.ranges_json()
            mins, maxs = data.get("servo_mins"), data.get("servo_maxs")
            for j in range(5):
                if not (self.hard_mins[j] <= mins[j] < maxs[j] <= self.hard_maxs[j]) or maxs[j] - mins[j] < 6:
                    return 400, {"ok": False, "error": f"joint {j} out of range"}
            self.saved_mins, self.saved_maxs = list(mins), list(maxs)
            self.soft_mins, self.soft_maxs = list(mins), list(maxs)
            return 200, {"ok": True}
        if p == "/ranges/explore":
            if not self.sensor:
                return 409, {"ok": False, "error": "no current sensor"}
            if self.explore_error:
                return self.explore_error[0], {"ok": False, "error": self.explore_error[1]}
            j, d = data["joint"], data["dir"]
            safe = self.explore_safe.get((j, d))
            if safe is None:
                safe = self.hard_mins[j] + 4 if d == "min" else self.hard_maxs[j] - 4
                reason = "hard_limit"
            else:
                reason = "bump"
            return 200, {"ok": True, "joint": j, "dir": d, "stopped_at": safe - 4 if d == "min" else safe + 4,
                         "reason": reason, "safe": safe, "peak_ma": 800}
        if p == "/bumps/clear":
            self.bumps = []
            self.soft_mins, self.soft_maxs = list(self.saved_mins), list(self.saved_maxs)
            return 200, {"ok": True}
        return 404, {"ok": False, "error": "not found"}


class FakeTranscriber(Transcriber):
    """``fn(audio_seconds, call_index) -> text``."""

    name = "fake"

    def __init__(self, fn):
        self.fn = fn
        self.calls: list[float] = []

    def transcribe(self, audio, prompt=None):
        secs = len(audio) / 16000.0
        self.calls.append(secs)
        return self.fn(secs, len(self.calls) - 1)


class FakeTTS(TTSBackend):
    """Tone, length ~ text, produced at 16 kHz on purpose so the brain must
    convert it to the robot's 22050 Hz."""

    name = "fake"

    def __init__(self):
        self.texts: list[str] = []

    def synthesize(self, text: str) -> bytes:
        from chipbrain.audio import to_robot_wav

        self.texts.append(text)
        secs = 0.06 * max(1, len(text.split()))
        t = np.arange(int(secs * 16000)) / 16000
        return to_robot_wav(encode_wav(float_to_int16(0.2 * np.sin(2 * np.pi * 300 * t)), 16000))


class FakeLLM(LLMBackend):
    """Scripted rounds. Each round: {"text": [...deltas], "tools": [(name, input), ...]},
    optionally "delay" (seconds before the first event) or "error" (raise instead)."""

    def __init__(self, rounds: list[dict]):
        self.rounds = list(rounds)
        self.calls: list[dict] = []

    async def stream(self, *, model, system, messages, tools, max_tokens):
        import copy

        self.calls.append({"model": model, "system": system, "messages": copy.deepcopy(messages), "tools": tools})
        r = self.rounds.pop(0) if self.rounds else {"text": ["Okay."]}
        if r.get("delay"):
            await asyncio.sleep(r["delay"])
        if r.get("error"):
            raise ConnectionError(r["error"])
        content = []
        text = "".join(r.get("text", []))
        for d in r.get("text", []):
            yield ("text", d)
        if text:
            content.append({"type": "text", "text": text})
        for i, (name, inp) in enumerate(r.get("tools", [])):
            content.append({"type": "tool_use", "id": f"tu_{len(self.calls)}_{i}", "name": name, "input": inp})
        yield ("final", content, "tool_use" if r.get("tools") else "end_turn")


def speech_like(seconds: float, rate: int = 22050, amp: float = 0.3, seed: int = 0) -> np.ndarray:
    """Voiced-ish signal: harmonics of 150 Hz with a syllable envelope."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    x = sum(np.sin(2 * np.pi * 150 * k * t + rng.uniform(0, 6)) / k for k in range(1, 8))
    env = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 3 * t))
    x = x * env
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


def silence(seconds: float, rate: int = 22050, amp: float = 0.0005, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (amp * rng.standard_normal(int(seconds * rate))).astype(np.float32)


class FakeOllama:
    """A tiny HTTP server speaking Ollama's native /api/chat streaming format
    (newline-delimited JSON, docs/api.md "Generate a chat completion").

    ``replies``: one entry per request, either a list of chunk dicts (streamed
    as NDJSON, status 200) or a ``(status, body_dict)`` tuple.
    """

    def __init__(self, replies: list, models: list[str] | None = None):
        self.replies = list(replies)
        self.models = list(models or [])
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path != "/api/tags":
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps({"models": [{"name": m, "model": m} for m in outer.models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append({"path": self.path, "body": body})
                reply = outer.replies.pop(0) if outer.replies else [{"message": {"role": "assistant", "content": "Okay."}, "done": False}, {"done": True, "done_reason": "stop"}]
                if isinstance(reply, tuple):
                    status, obj = reply
                    data = json.dumps(obj).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for chunk in reply:
                    if "sleep" in chunk:
                        time.sleep(chunk["sleep"])
                        continue
                    self.wfile.write(json.dumps({"model": body.get("model"), **chunk}).encode() + b"\n")
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeOllama":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def ollama_text(*parts: str) -> list[dict]:
    """Chunks for a plain streamed answer."""
    return [{"message": {"role": "assistant", "content": p}, "done": False} for p in parts] + [
        {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "eval_count": 5}]


def ollama_tool(name: str, args: dict, text: str = "") -> list[dict]:
    """Chunks for an answer that calls one tool (tool_calls arrive in a non-final chunk)."""
    chunks = [{"message": {"role": "assistant", "content": text}, "done": False}] if text else []
    return chunks + [
        {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]},
         "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}]
