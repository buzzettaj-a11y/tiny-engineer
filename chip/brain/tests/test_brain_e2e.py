"""End to end: UDP mic packets and hook HTTP posts into a real Brain, which
drives a fake robot over real HTTP. Only STT, TTS and Claude are faked."""

import asyncio
import json
import socket
import time

import numpy as np
import pytest

from chipbrain.app import Brain
from chipbrain.audio import float_to_int16
from chipbrain.config import Config
from chipbrain.packets import FLAG_MUTED, FLAG_SPEAKER, build_packet
from chipbrain.robot import Transport
from fakes import FakeLLM, FakeTranscriber, FakeTTS, silence, speech_like


def make_cfg(robot, tmp_path, **wake):
    cfg = Config(base_dir=tmp_path)
    cfg.robot.url = robot.url
    cfg.brain.http_host = "127.0.0.1"
    cfg.brain.http_port = 0
    cfg.brain.udp_port = 0
    cfg.ranges.enabled = False
    cfg.idle.enabled = False
    cfg.audio.vad = "energy"
    cfg.wake.follow_up_s = wake.get("follow_up_s", 1.5)
    cfg.wake.ack = wake.get("ack", "Yes?")
    return cfg


class Mic:
    """Sends audio to the brain like the robot does: 256-sample CHP1 packets."""

    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.port = port
        self.seq = 100

    async def send(self, x, flags=0):
        pcm = float_to_int16(x)
        for i in range(0, len(pcm), 256):
            self.sock.sendto(build_packet(self.seq, self.seq * 12, flags, pcm[i:i + 256]), ("127.0.0.1", self.port))
            self.seq += 1
            if self.seq % 40 == 0:
                await asyncio.sleep(0.003)  # don't overflow the UDP buffer
        await asyncio.sleep(0.05)

    async def heartbeat(self, flags=FLAG_MUTED):
        self.sock.sendto(build_packet(self.seq, 0, flags, None), ("127.0.0.1", self.port))
        self.seq += 1
        await asyncio.sleep(0.05)


async def until(cond, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def http(brain, method, path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    status, raw = await asyncio.to_thread(
        Transport().request, method, f"http://127.0.0.1:{brain.http.port}{path}", data, h, 5)
    return status, json.loads(raw)


def hook(name, host="VM-2", sid="s1", cwd=r"C:\code\JobSearch", **kw):
    return {"hook_event_name": name, "host": host, "session_id": sid, "cwd": cwd,
            "received_from": "chip-hook/1", **kw}


def test_voice_wake_early_listening_reply_and_follow_up(robot, tmp_path):
    script = ["Hey Chip, what",  # the 1.2 s prefix
              "Hey Chip, what is JobSearch doing?",
              "And how long has it been running?",  # follow-up, no wake word
              "I need to buy milk later"]  # after the window: not for Chip

    def stt(secs, i):
        return script[i]

    llm = FakeLLM([
        {"text": ["JobSearch is running ", "tests. Two ", "failed so far."]},
        {"text": ["About four minutes."]},
    ])
    tts, tr = FakeTTS(), FakeTranscriber(stt)

    async def go():
        brain = Brain(make_cfg(robot, tmp_path), transcriber=tr, tts=tts, llm=llm, wake_detector=None)
        await brain.start()
        try:
            await http(brain, "POST", "/events", hook("UserPromptSubmit", prompt="fix login tests"))
            await until(lambda: "Watching JobSearch on VM 2." in tts.texts)
            await until(lambda: not brain.robot.busy)
            mic = Mic(brain.udp_port)
            await mic.send(silence(0.5))
            await mic.send(speech_like(1.6))  # still talking...
            await until(lambda: "listening" in robot.anims())
            assert llm.calls == []  # turned to Joe BEFORE he finished
            assert tr.calls[0] == pytest.approx(1.2, abs=0.05)
            await mic.send(speech_like(0.8, seed=4))
            await mic.send(silence(0.9))
            await until(lambda: len(llm.calls) == 1 and brain.conv.state == "idle" and brain.conv.last_reply)
            first = llm.calls[0]
            assert first["messages"][-1] == {"role": "user", "content": "what is JobSearch doing?"}
            assert "FOCUSED: JobSearch on VM-2" in first["system"] and "fix login tests" in first["system"]
            assert tts.texts[-2:] == ["JobSearch is running tests.", "Two failed so far."]
            # follow-up without the wake word, inside the window
            await mic.send(speech_like(0.7, seed=9))  # short: no prefix check
            await mic.send(silence(0.9))
            await until(lambda: len(llm.calls) == 2 and brain.conv.state == "idle")
            msgs = llm.calls[1]["messages"]
            assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
            assert msgs[1]["content"] == "JobSearch is running tests. Two failed so far."
            # window over -> no wake word means not for Chip
            await asyncio.sleep(1.7)
            await brain.tick()
            await mic.send(speech_like(0.7, seed=11))
            await mic.send(silence(0.9))
            await until(lambda: brain.last_ignored == "I need to buy milk later")
            assert len(llm.calls) == 2 and len(tr.calls) == 4
            st = brain.status()
            assert st["conversation"]["last_transcript"] == "And how long has it been running?"
            assert st["mic"]["streaming"] and st["mic"]["lost"] == 0
        finally:
            await brain.stop()

    asyncio.run(go())
    assert robot.overlaps == 0
    plays = robot.plays()
    assert plays and all(p["wav_ok"] and p["has_length"] for p in plays)
    first_listen = next(r["t"] for r in robot.requests if r["path"] == "/anim?name=listening")
    reply_play = [r for r in plays if r["t"] > first_listen]
    assert reply_play, "reply audio must come after the early listening pose"
    assert robot.anims()[-1] in ("none", "reading")  # restored after the conversation


def test_wake_word_only_acks_then_accepts_follow_up(robot, tmp_path):
    texts = iter(["Chip?", "Switch to Flipopolis please"])
    llm = FakeLLM([{"text": ["Okay."], "tools": [("set_focus", {"session": "flipopolis"})]},
                   {"text": ["Watching Flipopolis now."]}])
    tts = FakeTTS()

    async def go():
        brain = Brain(make_cfg(robot, tmp_path, follow_up_s=3), transcriber=FakeTranscriber(lambda s, i: next(texts)),
                      tts=tts, llm=llm, wake_detector=None)
        await brain.start()
        try:
            brain.on_hook(hook("UserPromptSubmit"))
            brain.on_hook(hook("UserPromptSubmit", host="VM-1", sid="f", cwd="/home/joe/Flipopolis"))
            mic = Mic(brain.udp_port)
            await mic.send(silence(0.4))
            await mic.send(speech_like(0.6))
            await mic.send(silence(0.8))
            await until(lambda: "Yes?" in tts.texts)
            assert brain.conv.state == "listening"
            await mic.send(speech_like(1.0, seed=2))
            await mic.send(silence(0.8))
            await until(lambda: len(llm.calls) == 2 and brain.conv.state == "idle")
            assert brain.focus.focused.folder == "Flipopolis" and brain.focus.mode == "explicit"
            assert "Switching to" not in " ".join(tts.texts)  # Claude says it, no double announcement
        finally:
            await brain.stop()

    asyncio.run(go())


def test_speaker_audio_and_mute_are_discarded(robot, tmp_path):
    tr = FakeTranscriber(lambda s, i: "Hey Chip, hello")

    async def go():
        brain = Brain(make_cfg(robot, tmp_path), transcriber=tr, tts=FakeTTS(), llm=FakeLLM([]), wake_detector=None)
        await brain.start()
        try:
            mic = Mic(brain.udp_port)
            await mic.send(silence(0.3))
            await mic.send(speech_like(1.5), flags=FLAG_SPEAKER)  # Chip hearing itself
            await mic.send(silence(0.9), flags=FLAG_SPEAKER)
            await asyncio.sleep(0.3)
            assert tr.calls == [] and brain.stats.speaker_dropped > 0
            await mic.send(speech_like(0.9))
            assert brain.segmenter.in_speech
            await mic.heartbeat(FLAG_MUTED)  # button pressed mid-sentence
            assert not brain.segmenter.in_speech and brain.stats.muted
            assert brain.status()["mic"]["muted"]
            mic.seq += 5  # lost packets are counted
            await mic.send(silence(0.1))
            assert brain.stats.lost == 5
            assert tr.calls == []
        finally:
            await brain.stop()

    asyncio.run(go())


def test_hooks_focus_animations_heads_up_and_routes(robot, tmp_path):
    tts = FakeTTS()

    async def go():
        cfg = make_cfg(robot, tmp_path)
        cfg.brain.token = "tok"
        brain = Brain(cfg, transcriber=FakeTranscriber(lambda s, i: ""), tts=tts, llm=None, wake_detector=None)
        await brain.start()
        try:
            auth = {"X-Chip-Token": "tok"}
            st, body = await http(brain, "POST", "/events", hook("SessionStart", source="startup"))
            assert st == 401
            st, body = await http(brain, "POST", "/events", hook("SessionStart", source="startup"), auth)
            assert st == 200 and body["session"] == "JobSearch on VM-2" and body["focused"]
            await until(lambda: "welcome" in robot.anims())
            await http(brain, "POST", "/events", hook("PreToolUse", tool_name="Edit",
                                                     tool_input={"file_path": "C:/code/JobSearch/a.py"}), auth)
            await until(lambda: robot.anims()[-1] == "typing")
            # another session: no animation, but a heads-up when it waits
            other = dict(host="VM-1", sid="f", cwd="/home/joe/Flipopolis")
            await http(brain, "POST", "/events", hook("PreToolUse", tool_name="Read", **other), auth)
            await http(brain, "POST", "/events", hook("Stop", **other), auth)
            await until(lambda: "Heads up, Flipopolis on VM 1 is waiting for you." in tts.texts)
            await http(brain, "POST", "/events", hook("Notification", message="needs input", **other), auth)
            await asyncio.sleep(0.2)
            assert sum("Heads up" in t for t in tts.texts) == 1  # rate limited
            assert "reading" not in robot.anims()
            st, status = await http(brain, "GET", "/status")
            assert st == 200 and status["focus"]["label"] == "JobSearch on VM-2"
            assert {s["label"] for s in status["sessions"]} == {"JobSearch on VM-2", "Flipopolis on VM-1"}
            st, body = await http(brain, "POST", "/focus", {"session": "flipopolis"}, auth)
            assert st == 200 and body["focus"]["label"] == "Flipopolis on VM-1"
            st, body = await http(brain, "POST", "/focus", {"session": "nonexistent"}, auth)
            assert st == 400 and not body["ok"]
            st, body = await http(brain, "POST", "/focus", {"session": None}, auth)
            assert body["focus"]["mode"] == "auto"
            st, body = await http(brain, "POST", "/say", {"text": "Testing one two."}, auth)
            assert st == 200
            await until(lambda: "Testing one two." in tts.texts)
            st, body = await http(brain, "POST", "/events", {"no": "name"}, auth)
            assert st == 400
            st, _ = await http(brain, "GET", "/nope")
            assert st == 404
            st, _ = await http(brain, "GET", "/events")
            assert st == 405
        finally:
            await brain.stop()

    asyncio.run(go())
    assert robot.overlaps == 0


def test_hook_animations_held_during_conversation(robot, tmp_path):
    async def go():
        brain = Brain(make_cfg(robot, tmp_path), transcriber=FakeTranscriber(lambda s, i: ""), tts=FakeTTS(),
                      llm=FakeLLM([{"text": ["Hi Joe."]}]), wake_detector=None)
        await brain.start()
        try:
            brain.on_hook(hook("UserPromptSubmit"))
            await until(lambda: "reading" in robot.anims())
            await brain.respond("hello")
            brain.on_hook(hook("PreToolUse", tool_name="Bash"))
            await asyncio.sleep(0.2)
            assert "typing" not in robot.anims()  # still looking at Joe
            await asyncio.sleep(1.6)
            await brain.tick()  # follow-up window over
            await until(lambda: robot.anims()[-1] == "typing")
        finally:
            await brain.stop()

    asyncio.run(go())


def test_tools_mute_list_explore_adjust(robot, tmp_path):
    tts = FakeTTS()

    async def go():
        brain = Brain(make_cfg(robot, tmp_path), transcriber=FakeTranscriber(lambda s, i: ""), tts=tts,
                      llm=None, wake_detector=None)
        await brain.start()
        try:
            brain.on_hook(hook("UserPromptSubmit", prompt="add tests"))
            out = await brain.run_tool("list_sessions", {})
            assert "* JobSearch on VM-2" in out
            assert "add tests" in await brain.run_tool("session_detail", {"session": "jobsearch"})
            assert await brain.run_tool("mute_mic", {"muted": True}) == "Mic muted."
            assert robot.muted
            robot.explore_safe = {(1, "min"): 20, (1, "max"): 160}
            assert "Started" in await brain.run_tool("find_ranges", {"joints": ["neck"]})
            await until(lambda: any(t.startswith("Done.") for t in tts.texts), timeout=8)
            assert "Checking my neck." in tts.texts
            assert robot.saved_mins[1] == 20 and robot.saved_maxs[1] == 160
            out = await brain.run_tool("adjust_range", {"joint": "neck", "dir": "max", "action": "tighten",
                                                        "degrees": 10})
            assert "tightened" in out and robot.saved_maxs[1] == 150
            out = await brain.run_tool("adjust_range", {"joint": "neck", "dir": "max", "action": "loosen",
                                                        "degrees": 20})
            assert robot.saved_maxs[1] == 160  # capped at explored value
            assert "Unknown tool" in await brain.run_tool("bogus", {})
        finally:
            await brain.stop()

    asyncio.run(go())
    paths = robot.paths()
    assert paths.count("/ranges/explore") == 2


def test_idle_glance_only_when_robot_is_idle(robot, tmp_path):
    async def go():
        cfg = make_cfg(robot, tmp_path)
        cfg.idle.enabled = True
        brain = Brain(cfg, transcriber=FakeTranscriber(lambda s, i: ""), tts=FakeTTS(), llm=None, wake_detector=None)
        await brain.start()
        try:
            brain.idle.next_glance_at = 0
            robot.animation = "typing"
            await brain.tick()
            await asyncio.sleep(0.1)
            assert "/pose" not in robot.paths()
            robot.animation = "none"
            brain.idle.next_glance_at = 0
            await brain.tick()
            await until(lambda: "/pose" in robot.paths())
            req = [r for r in robot.requests if r["path"] == "/pose"][0]
            body = json.loads(req["body"])
            assert len(body["pose"]) == 5 and 100 <= body["ms"] <= 5000
        finally:
            await brain.stop()

    asyncio.run(go())


def test_range_coach_mention_waits_for_quiet(robot, tmp_path):
    tts = FakeTTS()
    robot.soft_maxs[0] = 136
    robot.bumps = [{"t_ms": 1000, "joint": 0, "angle": 139, "dir": "max"},
                   {"t_ms": 9000, "joint": 0, "angle": 138, "dir": "max"}]

    async def go():
        brain = Brain(make_cfg(robot, tmp_path), transcriber=FakeTranscriber(lambda s, i: ""), tts=tts,
                      llm=None, wake_detector=None)
        await brain.start()
        try:
            brain.conv.start_reply()  # mid-conversation
            await brain.coach.poll_once()
            assert robot.saved_maxs[0] == 136
            await asyncio.sleep(0.2)
            assert not any("bumping" in t for t in tts.texts)
            brain.conv.reply_done(brain.clock() - 10)
            await brain.tick()
            await until(lambda: any("bumping my head" in t for t in tts.texts))
        finally:
            await brain.stop()

    asyncio.run(go())


def test_bad_packets_counted(robot, tmp_path):
    async def go():
        brain = Brain(make_cfg(robot, tmp_path), transcriber=FakeTranscriber(lambda s, i: ""), tts=FakeTTS(),
                      llm=None, wake_detector=None)
        await brain.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(b"garbage", ("127.0.0.1", brain.udp_port))
            s.sendto(build_packet(1, 0, 0, np.zeros(256, np.int16))[:-4], ("127.0.0.1", brain.udp_port))
            await until(lambda: brain.stats.bad == 2)
        finally:
            await brain.stop()

    asyncio.run(go())


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_real_chip_hook_js_posts_into_brain(robot, tmp_path):
    import os
    import subprocess
    from pathlib import Path

    hook_js = Path(__file__).resolve().parents[2] / "hooks" / "chip-hook.js"

    async def go():
        cfg = make_cfg(robot, tmp_path)
        cfg.brain.token = "tok"
        brain = Brain(cfg, transcriber=FakeTranscriber(lambda s, i: ""), tts=FakeTTS(), llm=None, wake_detector=None)
        await brain.start()
        try:
            env = {**os.environ, "CHIP_BRAIN_URL": f"http://127.0.0.1:{brain.http.port}", "CHIP_BRAIN_TOKEN": "tok"}
            ev = {"hook_event_name": "PreToolUse", "session_id": "real", "cwd": "/home/joe/Flipopolis",
                  "tool_name": "Write", "tool_input": {"file_path": "/home/joe/Flipopolis/a.ts", "content": "x" * 99999}}
            r = await asyncio.to_thread(subprocess.run, ["node", str(hook_js)], input=json.dumps(ev).encode(),
                                        env=env, capture_output=True, timeout=10)
            assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
            s = brain.store.get("real")
            assert s is not None and s.label == f"Flipopolis on {socket.gethostname()}"
            assert s.tools[-1] == "Write a.ts"
            assert brain.focus.focused is s
            await until(lambda: "typing" in robot.anims())
        finally:
            await brain.stop()

    asyncio.run(go())
