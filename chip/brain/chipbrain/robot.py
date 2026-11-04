"""The ONLY way the brain talks to the robot.

The robot's web server handles one request at a time and ``/play`` blocks
until the clip ends, so every call goes through one worker:

1. urgent animations (``listening``: turn to Joe now)
2. speech clips, FIFO; clips already queued back to back are merged into one
   WAV so there is no gap between sentences
3. control calls (``/pose``, ``/ranges``, ``/mic``, health...), FIFO
4. the latest hook animation; older ones are coalesced away and anything
   older than ``anim_stale_s`` is dropped

503 ``busy`` is retried with a short back-off.
"""

from __future__ import annotations

import asyncio
import http.client
import json as jsonlib
import logging
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .audio import concat_wavs, wav_duration_s

log = logging.getLogger("chip.robot")


class RobotError(Exception):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class Transport:
    """Blocking HTTP. Swappable for tests."""

    def request(self, method: str, url: str, body: bytes | None, headers: dict, timeout: float) -> tuple[int, bytes]:
        u = urllib.parse.urlsplit(url)
        conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(u.hostname, u.port, timeout=timeout)
        try:
            path = u.path or "/"
            if u.query:
                path += "?" + u.query
            hdrs = dict(headers)
            hdrs.setdefault("Connection", "close")
            if body is not None:
                hdrs["Content-Length"] = str(len(body))
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()


@dataclass
class _Item:
    kind: str  # "speech" | "control" | "anim"
    method: str = "GET"
    path: str = "/"
    body: bytes | None = None
    headers: dict = field(default_factory=dict)
    timeout: float = 10.0
    fut: asyncio.Future | None = None
    wav: bytes = b""
    pose: str = "talking"
    anim: str = ""
    queued_at: float = 0.0
    merged: list = field(default_factory=list)  # speech items folded into this one


class RobotClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        transport: Transport | None = None,
        anim_stale_s: float = 8.0,
        busy_retries: int = 8,
        busy_delay_s: float = 0.4,
        clock=time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport or Transport()
        self.anim_stale_s = anim_stale_s
        self.busy_retries = busy_retries
        self.busy_delay_s = busy_delay_s
        self.clock = clock
        self._speech: deque[_Item] = deque()
        self._control: deque[_Item] = deque()
        self._anim: _Item | None = None
        self._urgent: _Item | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.reachable: bool | None = None
        self.last_error: str = ""
        self.last_ok_at: float | None = None
        self.last_anim_sent: str | None = None
        self.playing = False
        self.speech_queued = 0
        self.calls = 0

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="robot-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        for q in (self._speech, self._control):
            while q:
                it = q.popleft()
                if it.fut and not it.fut.done():
                    it.fut.cancel()

    @property
    def busy(self) -> bool:
        """Speaking now or speech waiting."""
        return self.playing or bool(self._speech)

    # ---- public API --------------------------------------------------------
    def set_anim(self, name: str, urgent: bool = False) -> None:
        it = _Item(kind="anim", method="POST", path=f"/anim?name={urllib.parse.quote(name)}", anim=name,
                   queued_at=self.clock(), timeout=5.0)
        if urgent:
            self._urgent = it
        else:
            self._anim = it  # coalesce: latest wins
        self._wake.set()

    def enqueue_play(self, wav: bytes, pose: str = "talking") -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._speech.append(_Item(kind="speech", wav=wav, pose=pose, fut=fut, queued_at=self.clock()))
        self.speech_queued += 1
        self._wake.set()
        return fut

    async def play(self, wav: bytes, pose: str = "talking") -> dict | None:
        return await self.enqueue_play(wav, pose)

    async def call(self, method: str, path: str, json: Any = None, timeout: float = 10.0) -> Any:
        """Serialized JSON call. Returns parsed JSON; raises RobotError on HTTP
        errors (with status/body) or when the robot is unreachable."""
        body = None
        headers = {}
        if json is not None:
            body = jsonlib.dumps(json).encode()
            headers["Content-Type"] = "application/json"
        fut = asyncio.get_running_loop().create_future()
        self._control.append(_Item(kind="control", method=method, path=path, body=body, headers=headers,
                                   timeout=timeout, fut=fut, queued_at=self.clock()))
        self._wake.set()
        return await fut

    async def get(self, path: str, timeout: float = 5.0) -> Any:
        return await self.call("GET", path, timeout=timeout)

    async def post(self, path: str, json: Any = None, timeout: float = 10.0) -> Any:
        return await self.call("POST", path, json=json if json is not None else {}, timeout=timeout)

    async def pose(self, pose: list, ms: int) -> Any:
        return await self.post("/pose", {"pose": pose, "ms": int(max(100, min(5000, ms)))})

    # ---- worker -------------------------------------------------------------
    def _next(self) -> _Item | None:
        if self._urgent is not None:
            it, self._urgent = self._urgent, None
            return it
        if self._speech:
            first = self._speech.popleft()
            merged = [first]
            total = wav_duration_s(first.wav) if first.wav else 0.0
            while self._speech and self._speech[0].pose == first.pose and total < 20.0:
                nxt = self._speech.popleft()
                total += wav_duration_s(nxt.wav)
                merged.append(nxt)
            if len(merged) > 1:
                first = _Item(kind="speech", pose=first.pose, wav=concat_wavs([m.wav for m in merged]),
                              queued_at=first.queued_at, merged=merged)
            return first
        if self._control:
            return self._control.popleft()
        if self._anim is not None:
            it, self._anim = self._anim, None
            if self.clock() - it.queued_at > self.anim_stale_s:
                log.debug("dropping stale animation %s", it.anim)
                return self._next()
            return it
        return None

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                it = self._next()
                if it is None:
                    break
                await self._execute(it)

    def _headers(self, extra: dict) -> dict:
        h = dict(extra)
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _execute(self, it: _Item) -> None:
        if it.kind == "speech":
            path = "/play" if it.pose == "talking" else f"/play/{it.pose}"
            dur = wav_duration_s(it.wav)
            req = dict(method="POST", path=path, body=it.wav, headers={"Content-Type": "audio/wav"},
                       timeout=dur + 15.0)
            self.playing = True
        else:
            req = dict(method=it.method, path=it.path, body=it.body, headers=it.headers, timeout=it.timeout)
        futs = [m.fut for m in it.merged] if it.merged else [it.fut]
        try:
            for attempt in range(self.busy_retries + 1):
                try:
                    status, raw = await asyncio.to_thread(
                        self.transport.request, req["method"], self.base_url + req["path"], req["body"],
                        self._headers(req["headers"]), req["timeout"],
                    )
                except Exception as e:  # network
                    self._mark_down(str(e))
                    self._fail(futs, RobotError(f"robot unreachable: {e}"))
                    return
                self.calls += 1
                data = _parse(raw)
                if status == 503 and _err(data) == "busy" and attempt < self.busy_retries:
                    if it.kind == "anim" and (self._anim is not None or self._urgent is not None):
                        return  # superseded while we waited
                    await asyncio.sleep(self.busy_delay_s * (1 + attempt * 0.5))
                    continue
                self._mark_up()
                if status >= 400:
                    msg = _err(data) or f"HTTP {status}"
                    if it.kind == "anim":
                        log.warning("robot refused animation %s: %s", it.anim, msg)
                    self._fail(futs, RobotError(f"{req['method']} {req['path']}: {msg}", status, data))
                    return
                if it.kind == "anim":
                    self.last_anim_sent = it.anim
                for f in futs:
                    if f and not f.done():
                        f.set_result(data)
                return
        finally:
            if it.kind == "speech":
                self.playing = False

    def _fail(self, futs, exc: Exception) -> None:
        for f in futs:
            if f is None:
                continue
            if not f.done():
                if f.get_loop().is_closed():
                    continue
                f.set_exception(exc)
        # speech futures that nobody awaits must not warn "exception never retrieved"
        for f in futs:
            if f is not None and f.done() and not f.cancelled():
                f.exception()

    def _mark_down(self, err: str) -> None:
        if self.reachable is not False:
            log.warning("robot unreachable at %s: %s", self.base_url, err)
        self.reachable = False
        self.last_error = err

    def _mark_up(self) -> None:
        if self.reachable is False:
            log.info("robot reachable again")
        self.reachable = True
        self.last_ok_at = self.clock()

    def as_dict(self) -> dict:
        return {
            "url": self.base_url,
            "reachable": self.reachable,
            "last_error": self.last_error,
            "playing": self.playing,
            "speech_queue": len(self._speech),
            "control_queue": len(self._control),
            "last_anim_sent": self.last_anim_sent,
        }


def _parse(raw: bytes) -> Any:
    try:
        return jsonlib.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return {"raw": raw[:200].decode("latin-1")}


def _err(data: Any) -> str:
    return str(data.get("error", "")) if isinstance(data, dict) else ""
