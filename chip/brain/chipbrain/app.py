"""The brain: wires mic -> wake -> STT -> Claude -> TTS -> robot, plus hook
intake, focus, heads-up, range coach and idle personality."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import __version__
from .animmap import CONTINUOUS, animation_for_event
from .audio import ROBOT_RATE, STT_RATE, StreamResampler, float_to_int16, int16_to_float
from .claude import AnthropicBackend, LLMBackend
from .config import Config
from .conversation import Conversation
from .focus import FocusChange, FocusManager, HeadsUp, spoken
from .httpserver import JsonHttpServer
from .idle import IdleContext, IdlePersonality
from .memory import Memory
from .mind import Mind
from .packets import PacketError, StreamStats, parse_packet
from .rangecoach import RangeCoach, State
from .robot import RobotClient, RobotError, Transport
from .sessions import SessionStore
from .speech import Speaker
from .stt import Transcriber, make_transcriber
from .tts import TTSBackend, make_tts
from .vad import Noise, Prefix, Segment, Segmenter, SpeechStart, make_vad
from .wake import WakeDetector, make_detector, match_wake

log = logging.getLogger("chip")

# Whisper likes to "hear" these in noise. Ignored unless Chip was clearly woken.
HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you", "bye", "thanks", "okay",
    "so", "uh", "um", "hmm", "", "subtitles by the amaraorg community",
}

AUTO = object()


@dataclass
class _SegMeta:
    started_at: float  # brain clock time the speech started
    woken: bool = False  # wake word already heard (prefix check / openWakeWord)
    in_window: bool = False


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, brain: "Brain"):
        self.brain = brain

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.brain.on_datagram(data, addr)
        except Exception:
            log.exception("mic packet handling crashed")


class Brain:
    def __init__(
        self,
        cfg: Config,
        *,
        transport: Transport | None = None,
        transcriber: Transcriber | None = None,
        tts: TTSBackend | None = None,
        llm: Any = AUTO,
        local_llm: Any = AUTO,
        wake_detector: Any = AUTO,
        clock=time.monotonic,
        idle: IdlePersonality | None = None,
    ):
        self.cfg = cfg
        self.clock = clock
        self.started_at = clock()
        self.robot = RobotClient(cfg.robot.url, cfg.robot.token, transport, cfg.robot.anim_stale_s, clock=clock)
        self.store = SessionStore(cfg.focus.forget_ended_after_s, cfg.focus.forget_idle_after_s)
        self.focus = FocusManager(self.store, cfg.focus)
        self.headsup = HeadsUp(cfg.focus)
        self.conv = Conversation(cfg.wake.follow_up_s, cfg.claude.history_turns)
        self.stats = StreamStats()
        self.resampler = StreamResampler(ROBOT_RATE, STT_RATE)
        self.segmenter = Segmenter(cfg.audio, make_vad(cfg.audio))
        self.wake_detector: WakeDetector | None = (
            make_detector(cfg.wake, cfg.resolve) if wake_detector is AUTO else wake_detector
        )
        self.stt = transcriber or make_transcriber(cfg.stt)
        self.tts = tts or make_tts(cfg.tts, cfg.resolve)
        self.llm: LLMBackend | None = self._make_llm() if llm is AUTO else llm
        self.local_llm: LLMBackend | None = self._make_local() if local_llm is AUTO else local_llm
        self.mind = Mind(cfg.local, cfg.claude, self.local_llm, self.llm, self.run_tool)
        self.state = State(cfg.resolve(cfg.brain.state_file))
        self.memory = Memory(cfg.resolve(cfg.brain.memory_file), cfg.brain.memory_max_items)
        self.stt_pool = ThreadPoolExecutor(1, thread_name_prefix="stt")
        self.tts_pool = ThreadPoolExecutor(1, thread_name_prefix="tts")
        self.speaker = Speaker(self.tts, self.robot, self.tts_pool)
        self.coach = RangeCoach(self.robot, cfg.ranges, self.state, say=self._say_wait, mention=self.announce)
        self.idle = idle or IdlePersonality(cfg.idle)
        self.mentions: deque[str] = deque(maxlen=5)
        self.held_anim: str | None = None
        self.last_transcript = ""
        self.last_ignored = ""
        self._seg: _SegMeta | None = None
        self._oww_fired_at: float | None = None
        self._utterances: asyncio.Queue | None = None
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self._idle_task: asyncio.Task | None = None
        self.http: JsonHttpServer | None = None
        self.udp_transport = None
        self.robot_anim: str | None = None
        self.robot_health: dict = {}

    # ------------------------------------------------------------------ setup
    def _make_llm(self) -> LLMBackend | None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log.warning("ANTHROPIC_API_KEY is not set: Chip can hear but not think")
            return None
        try:
            return AnthropicBackend()
        except ImportError:
            log.warning("anthropic package missing (pip install anthropic): Chip can hear but not think")
            return None

    def _make_local(self) -> LLMBackend | None:
        if not self.cfg.local.enabled:
            return None
        from .ollama import OllamaBackend

        log.info("local model: %s at %s (Claude answers if it can't)", self.cfg.local.model, self.cfg.local.url)
        return OllamaBackend(self.cfg.local)

    async def start(self, http: bool = True, udp: bool = True) -> None:
        loop = asyncio.get_running_loop()
        self._utterances = asyncio.Queue(maxsize=3)
        self.robot.start()
        if http:
            self.http = JsonHttpServer(self.handle_http, self.cfg.brain.http_host, self.cfg.brain.http_port)
            await self.http.start()
            log.info("hooks/status on http://%s:%d", self.cfg.brain.http_host, self.http.port)
        if udp:
            self.udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: _Udp(self), local_addr=("0.0.0.0", self.cfg.brain.udp_port))
            log.info("listening for the mic on UDP %d", self.udp_port)
        self._tasks += [
            loop.create_task(self._utterance_loop(), name="utterances"),
            loop.create_task(self._tick_loop(), name="tick"),
            loop.create_task(self._health_loop(), name="health"),
        ]
        if self.cfg.ranges.enabled:
            self._tasks.append(loop.create_task(self.coach.run(), name="range-coach"))
        loop.run_in_executor(self.stt_pool, self._warm_stt)
        loop.run_in_executor(self.tts_pool, self._warm_tts)

    @property
    def udp_port(self) -> int:
        if self.udp_transport is not None:
            return self.udp_transport.get_extra_info("sockname")[1]
        return self.cfg.brain.udp_port

    def _warm_stt(self) -> None:
        try:
            self.stt.load()
        except Exception as e:
            log.error("Whisper failed to load: %s", e)

    def _warm_tts(self) -> None:
        try:
            self.tts.warm()
        except Exception as e:
            log.error("TTS failed to load: %s", e)

    async def stop(self) -> None:
        for t in self._tasks + list(self._bg):
            t.cancel()
        for t in self._tasks + list(self._bg):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self.udp_transport:
            self.udp_transport.close()
        if self.http:
            await self.http.stop()
        await self.robot.stop()
        self.stt_pool.shutdown(wait=False, cancel_futures=True)
        self.tts_pool.shutdown(wait=False, cancel_futures=True)

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        t.add_done_callback(_log_task_error)
        return t

    # -------------------------------------------------------------- mic path
    def on_datagram(self, data: bytes, addr=None) -> None:
        now = self.clock()
        try:
            pkt = parse_packet(data)
        except PacketError:
            self.stats.bad += 1
            return
        if addr:
            self.stats.sender = addr[0]
        was_muted = self.stats.muted
        gap = self.stats.accept(pkt, now)
        if gap is None:
            return
        if pkt.muted != was_muted:
            log.info("mic %s", "muted" if pkt.muted else "unmuted")
        if pkt.heartbeat:
            if pkt.muted and self.segmenter.in_speech:
                self.segmenter.reset()
                self._seg = None
            return
        if gap:
            fill = min(gap, self.cfg.audio.max_gap_fill_packets) * max(1, pkt.samples.size)
            self._process_audio(np.zeros(fill, dtype=np.int16), moving=pkt.moving)
        samples = pkt.samples
        if pkt.speaker:
            # Chip must not hear itself: replace with silence (keeps timing,
            # lets an utterance in progress end naturally).
            self.stats.speaker_dropped += 1
            samples = np.zeros_like(samples)
        self._process_audio(samples, moving=pkt.moving)

    def _process_audio(self, s16: np.ndarray, moving: bool = False) -> None:
        x = self.resampler.process(int16_to_float(s16))
        if self.wake_detector is not None and x.size:
            if self.wake_detector.process(float_to_int16(x)):
                self._oww_fired_at = self.clock()
                self._early_wake("openWakeWord")
        for ev in self.segmenter.feed(x):
            self._on_vad(ev, moving)

    def _on_vad(self, ev, moving: bool) -> None:
        now = self.clock()
        if isinstance(ev, SpeechStart):
            started = now - max(0.0, self.segmenter.now - ev.t)
            woken = self._oww_fired_at is not None and now - self._oww_fired_at < 2.0
            self._seg = _SegMeta(started_at=started, woken=woken,
                                 in_window=self.conv.accepts_without_wake(started))
        elif isinstance(ev, Prefix):
            seg = self._seg
            if seg and not seg.woken and not seg.in_window and self.wake_detector is None:
                self._spawn(self._prefix_check(ev.audio, seg))
        elif isinstance(ev, Segment):
            seg = self._seg or _SegMeta(started_at=now - (ev.t_end - ev.t_start))
            self._seg = None
            if self._utterances is None:
                return
            try:
                self._utterances.put_nowait((ev.audio, seg))
            except asyncio.QueueFull:
                log.warning("still busy, dropped an utterance")
        elif isinstance(ev, Noise):
            self._seg = None
            if ev.loud and not moving and not self.robot.busy and self.idle.on_loud_sound(now):
                log.info("loud sound (%.0f dBFS)", ev.peak_dbfs)

    async def _transcribe(self, audio: np.ndarray, prompt: str | None = None) -> str:
        loop = asyncio.get_running_loop()
        return (await loop.run_in_executor(self.stt_pool, self.stt.transcribe, audio, prompt)).strip()

    async def _prefix_check(self, audio: np.ndarray, seg: _SegMeta) -> None:
        text = await self._transcribe(audio)
        if text and match_wake(text, self.cfg.wake).matched and not seg.woken:
            seg.woken = True
            self._early_wake(f'prefix "{text}"')

    def _early_wake(self, how: str) -> None:
        if self._seg:
            self._seg.woken = True
        log.info("wake (%s)", how)
        self.robot.set_anim("listening", urgent=True)

    async def _utterance_loop(self) -> None:
        while True:
            audio, seg = await self._utterances.get()
            try:
                await self.handle_utterance(audio, seg)
            except Exception:
                log.exception("utterance handling failed")

    async def handle_utterance(self, audio: np.ndarray, seg: _SegMeta) -> None:
        text = await self._transcribe(audio)
        now = self.clock()
        norm = re.sub(r"[^a-z ]", "", text.lower()).strip()
        if not text or (norm in HALLUCINATIONS and not seg.woken):
            if seg.woken:  # we already turned to Joe: wait for him, then relax
                self.conv.woke(now)
            return
        in_window = seg.in_window or self.conv.accepts_without_wake(seg.started_at)
        m = match_wake(text, self.cfg.wake)
        if m.matched:
            request = m.rest
        elif seg.woken or in_window:
            request = text
        else:
            self.last_ignored = text
            log.info("(not for me) %s", text)
            return
        self.last_transcript = text
        log.info("heard: %s", text)
        self.conv.last_heard = text
        if not request.strip(" .,!?"):
            self.conv.woke(now)
            self.robot.set_anim("listening", urgent=True)
            if self.cfg.wake.ack:
                await self.speaker.say(self.cfg.wake.ack)
                self.conv.woke(self.clock())
            return
        await self.respond(request)

    async def respond(self, request: str) -> str:
        self.conv.start_reply()
        self.robot.set_anim("listening", urgent=True)
        deltas = self.mind.reply(
            request, self.conv.messages(), self.context(),
            session_names=[s.folder for s in self.store.all()],
        )
        spoken = await self.speaker.speak_stream(deltas, on_first=self.conv.speaking)
        self.conv.add("user", request)
        self.conv.add("assistant", spoken)
        self.conv.last_reply = spoken
        self.conv.reply_done(self.clock())
        return spoken

    # -------------------------------------------------------- speech helpers
    async def _say_wait(self, text: str) -> None:
        await self.speaker.say(text)

    def announce(self, text: str) -> None:
        """Unprompted speech. Waits for the conversation to end first."""
        if self.conv.active(self.clock()) or self.coach.exploring:
            self.mentions.append(text)
        else:
            self._spawn(self.speaker.say(text))

    # ---------------------------------------------------------------- hooks
    def on_hook(self, ev: dict) -> dict:
        now = self.clock()
        s = self.store.ingest(ev, now)
        name = ev.get("hook_event_name", "")
        change = self.focus.on_event(s, ev, now)
        if change:
            self._on_focus_change(change, now)
        focused = self.focus.focused
        if s is focused:
            anim = animation_for_event(ev)
            if anim:
                self._drive_anim(anim)
            if name == "Stop":
                self.idle.on_task_finished(s.last_task_s, now)
        else:
            line = self.headsup.check(s, ev, focused, now)
            if line:
                log.info("heads-up: %s", line)
                self.announce(line)
        return {"ok": True, "session": s.label, "focused": s is focused}

    def _drive_anim(self, anim: str) -> None:
        if self.conv.active(self.clock()) or self.coach.exploring:
            self.held_anim = anim  # look at Joe while talking; replay after
            return
        self.robot.set_anim(anim)

    def _on_focus_change(self, change: FocusChange, now: float) -> None:
        if change.announce:
            self.announce(change.announce)
        s = change.session
        if s and s.last_anim in CONTINUOUS and s.idle_s(now) < 30:
            self._drive_anim(s.last_anim)

    def _conversation_ended(self, now: float) -> None:
        log.info("conversation over")
        f = self.focus.focused
        anim = self.held_anim
        self.held_anim = None
        if not (anim in CONTINUOUS and f and f.idle_s(now) < 30):
            anim = f.last_anim if f and f.last_anim in CONTINUOUS and f.idle_s(now) < 30 else "none"
        self.robot.set_anim(anim)

    # ----------------------------------------------------------------- loops
    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")

    async def tick(self) -> None:
        now = self.clock()
        if self.conv.expire(now):
            self._conversation_ended(now)
        change = self.focus.evaluate(now)
        if change:
            self._on_focus_change(change, now)
        for s in self.store.prune(now):
            log.info("forgot session %s", s.label)
        if self.mentions and not self.conv.active(now) and not self.robot.busy and not self.coach.exploring:
            self._spawn(self.speaker.say(self.mentions.popleft()))
        await self._idle_tick(now)

    def _idle_context(self, now: float) -> IdleContext:
        f = self.focus.focused
        return IdleContext(
            conversation_active=self.conv.active(now),
            busy=self.robot.busy or self.coach.exploring or bool(self.mentions)
            or (self._idle_task is not None and not self._idle_task.done()),
            focused_idle_s=f.idle_s(now) if f and not f.ended else None,
            asleep=self.stats.asleep,
        )

    async def _idle_tick(self, now: float) -> None:
        if not self.cfg.idle.enabled or not self.idle.due(now):
            return
        ctx = self._idle_context(now)
        if not self.idle.preconditions(ctx):
            self.idle.take(now, ctx)  # reschedules / drops stale triggers
            return
        try:
            ctx.robot_anim = (await self.robot.get("/anim")).get("animation")
        except RobotError:
            return
        self.robot_anim = ctx.robot_anim
        moves = self.idle.take(self.clock(), ctx)
        if moves:
            self._idle_task = self._spawn(self._perform(moves))

    async def _perform(self, moves) -> None:
        for mv in moves:
            if self.conv.active(self.clock()) or self.coach.exploring:
                return
            log.debug("idle %s %s", mv.kind, mv.pose)
            try:
                await self.robot.pose(mv.pose, mv.ms)
            except RobotError as e:
                log.debug("idle pose refused: %s", e)
                return
            if mv.hold_s:
                await asyncio.sleep(mv.hold_s)

    async def _health_loop(self) -> None:
        while True:
            try:
                self.robot_health = await self.robot.get("/health")
            except RobotError:
                pass
            await asyncio.sleep(self.cfg.robot.health_poll_s)

    # ------------------------------------------------------------------ tools
    async def run_tool(self, name: str, inp: dict) -> str:
        now = self.clock()
        if name == "list_sessions":
            sessions = self.store.all()
            if not sessions:
                return "No sessions reported yet."
            f = self.focus.focused
            return f"focus mode {self.focus.mode}\n" + "\n".join(
                ("* " if s is f else "- ") + s.describe(now, detail=False) for s in sessions)
        if name == "session_detail":
            s = self.store.find(str(inp.get("session", "")))
            return s.describe(now, detail=True) if s else "No such session."
        if name == "set_focus":
            ok, msg, change = self.focus.set_focus(inp.get("session"), now)
            if change:
                change.announce = None  # Claude says it in its own words
                self._on_focus_change(change, now)
            return msg
        if name == "mute_mic":
            muted = bool(inp.get("muted", True))
            try:
                r = await self.robot.post("/mic", {"muted": muted})
            except RobotError as e:
                if e.status == 409 and "switch" in str(e):
                    return ("Joe's mute switch on the robot controls the mic. "
                            "Tell him to flip it to O to mute, or to I to listen.")
                return f"Could not change the mic: {e}"
            return f"Mic {'muted' if r.get('muted', muted) else 'unmuted'}."
        if name == "find_ranges":
            if self.coach.exploring:
                return "Already exploring."
            from .rangecoach import parse_joint

            joints = [parse_joint(j) for j in inp.get("joints") or []]
            joints = [j for j in joints if j is not None] or None
            self._spawn(self._explore(joints))
            return "Started. It takes a minute or two; you narrate each joint as you go."
        if name == "adjust_range":
            try:
                return await self.coach.adjust(inp.get("joint"), inp.get("dir"), inp.get("action"),
                                               float(inp.get("degrees", 5)))
            except RobotError as e:
                return f"Robot refused: {e}"
        if name == "remember":
            return self.memory.remember(str(inp.get("fact", "")))
        if name == "forget":
            return self.memory.forget(str(inp.get("about", "")))
        return f"Unknown tool {name}."

    async def _explore(self, joints) -> None:
        # let the spoken reply finish before the first joint moves
        while self.conv.state in ("thinking", "speaking") or self.robot.busy:
            await asyncio.sleep(0.2)
        result = await self.coach.explore(joints)
        log.info("explore: %s", result)
        await self.speaker.say(result)
        self.robot.set_anim("none")

    # -------------------------------------------------------------- context
    def context(self) -> str:
        now = self.clock()
        auto = self.focus.mode == "auto"
        lines = []
        taught = self.memory.prompt()
        if taught:
            lines += [taught, ""]
        lines += [
            "Focus mode: " + ("auto (you follow the most recently active session)." if auto
                              else "explicit (Joe chose this session)."),
            "Sessions:",
            self.store.summary(now, self.focus.focused),
        ]
        if not self.stats.streaming(now):
            lines.append("Note: your microphone stream is not arriving right now.")
        if self.coach.exploring:
            lines.append("You are exploring your joint ranges right now.")
        if self.coach.log:
            lines.append("Recent range changes: " + "; ".join(self.coach.log[-3:]))
        return "\n".join(lines)

    def status(self) -> dict:
        now = self.clock()
        return {
            "ok": True,
            "version": __version__,
            "uptime_s": round(now - self.started_at),
            "focus": self.focus.as_dict(),
            "sessions": [s.brief(now) for s in self.store.all()],
            "mic": self.stats.as_dict(now) | {"udp_port": self.udp_port, "in_speech": self.segmenter.in_speech},
            "conversation": self.conv.as_dict(now) | {
                "last_transcript": self.last_transcript, "last_ignored": self.last_ignored,
                "last_reply": self.conv.last_reply, "model": self.mind.last_model,
                "answered_by": self.mind.last_engine,
            },
            "robot": self.robot.as_dict() | {"animation": self.robot_anim,
                                             "mic": self.robot_health.get("mic"),
                                             "current_ma": self.robot_health.get("current_ma")},
            "ranges": {"exploring": self.coach.exploring, "sensor": self.coach.sensor, "log": self.coach.log[-10:]},
            "voice": {"stt": self.stt.name, "tts": self.tts.name, "claude": self.llm is not None,
                      "wake": self.wake_detector.name if self.wake_detector else "whisper-prefix"},
        }

    # ------------------------------------------------------------------- http
    def _authorized(self, headers: dict) -> bool:
        tok = self.cfg.brain.token
        if not tok:
            return True
        return headers.get("x-chip-token") == tok or headers.get("authorization") == f"Bearer {tok}"

    async def handle_http(self, method: str, path: str, headers: dict, body: Any) -> tuple[int, Any]:
        routes = {"/events": "POST", "/status": "GET", "/say": "POST", "/focus": "POST"}
        if path not in routes:
            return 404, {"ok": False, "error": "not found"}
        if method == "OPTIONS":
            return 200, {"ok": True}
        if method != routes[path]:
            return 405, {"ok": False, "error": "method not allowed"}
        if path != "/status" and not self._authorized(headers):
            return 401, {"ok": False, "error": "unauthorized"}
        if path == "/status":
            return 200, self.status()
        if not isinstance(body, dict):
            return 400, {"ok": False, "error": "send a JSON object"}
        if path == "/events":
            if not isinstance(body.get("hook_event_name"), str):
                return 400, {"ok": False, "error": "missing hook_event_name"}
            return 200, self.on_hook(body)
        if path == "/say":
            text = str(body.get("text") or "").strip()
            if not text:
                return 400, {"ok": False, "error": "missing text"}
            self._spawn(self.speaker.say(text[:500]))
            return 200, {"ok": True, "queued": True}
        if path == "/focus":
            if "session" not in body:
                return 400, {"ok": False, "error": "missing session (id, label or null)"}
            ok, msg, change = self.focus.set_focus(body["session"], self.clock())
            if change:
                change.announce = f"Watching {spoken(change.session.label)}." if change.session else None
                self._on_focus_change(change, self.clock())
            return (200 if ok else 400), {"ok": ok, "message": msg, "focus": self.focus.as_dict()}
        return 404, {"ok": False, "error": "not found"}


def _log_task_error(t: asyncio.Task) -> None:
    if not t.cancelled() and t.exception():
        log.error("background task failed: %r", t.exception())
