import asyncio
import random
from datetime import datetime

from chipbrain.claude import TOOLS, ChipAgent, build_system, choose_model
from chipbrain.config import ClaudeCfg, IdleCfg
from chipbrain.idle import IdleContext, IdlePersonality
from fakes import FakeLLM


def idle(hour=14, **cfg):
    return IdlePersonality(IdleCfg(**cfg), rng=random.Random(1), hour=lambda: hour)


OK = IdleContext(conversation_active=False, busy=False, focused_idle_s=100, asleep=False, robot_anim="none")


def test_glances_only_when_everything_is_quiet():
    p = idle()
    p.due(0)  # schedules the first glance
    assert 20 <= p.next_glance_at <= 90
    p.next_glance_at = 10
    assert p.take(5, OK) == []
    moves = p.take(10, OK)
    assert len(moves) == 1 and moves[0].kind == "glance"
    pose = moves[0].pose
    assert len(pose) == 5 and pose[2] is None and pose[3] is None  # hands untouched
    assert all(v is None or abs(v) <= 0.2 for v in pose)
    assert 20 <= p.next_glance_at - 10 <= 90
    for blocked in [IdleContext(conversation_active=True, focused_idle_s=100, robot_anim="none"),
                    IdleContext(busy=True, focused_idle_s=100, robot_anim="none"),
                    IdleContext(focused_idle_s=30, robot_anim="none"),
                    IdleContext(focused_idle_s=100, robot_anim="typing"),
                    IdleContext(focused_idle_s=100, robot_anim="none", asleep=True)]:
        p.next_glance_at = 0
        assert p.take(1, blocked) == [], blocked
    p.next_glance_at = 0
    assert p.take(1, IdleContext(focused_idle_s=None, robot_anim="none"))  # no session at all is fine


def test_stretch_after_long_task_only():
    p = idle()
    p.next_glance_at = 1e9
    p.on_task_finished(60, 0)
    assert p.stretch_at is None
    p.on_task_finished(400, 0)
    assert p.take(5, OK) == []
    moves = p.take(9, OK)
    assert len(moves) >= 3 and all(m.kind == "stretch" for m in moves)
    assert moves[-1].pose[:2] == [0.0, 0.0]


def test_loud_sound_glance_rate_limited():
    p = idle()
    p.next_glance_at = 1e9
    assert p.on_loud_sound(0)
    m = p.take(0.5, OK)
    assert m and m[0].kind == "look" and m[0].pose[0] == 0.25
    assert not p.on_loud_sound(10)
    assert p.on_loud_sound(40)
    p.take(40.5, IdleContext(conversation_active=True, robot_anim="none"))
    assert not p.look_pending  # stale looks are dropped


def test_night_is_calmer():
    day, night = idle(14), idle(2)
    day.schedule_next(0)
    night.schedule_next(0)
    assert night.next_glance_at > day.next_glance_at
    assert night.night() and not day.night()
    g = [night.glance() for _ in range(3)]
    assert all(abs(v) <= 0.2 for m in g for v in m.pose if v is not None)
    late = idle(23)
    assert late.night() and not idle(7).night()


def test_choose_model():
    cfg = ClaudeCfg(fast_model="claude-haiku-5-5")
    assert choose_model(cfg, "how are you today", []) == "claude-haiku-5-5"
    assert choose_model(cfg, "what is JobSearch doing", ["JobSearch"]) == "claude-sonnet-5-5"
    assert choose_model(cfg, "are the tests passing", []) == "claude-sonnet-5-5"
    assert choose_model(ClaudeCfg(), "how are you", []) == "claude-sonnet-5-5"


def test_system_prompt_has_persona_and_context():
    s = build_system(ClaudeCfg(), "FOCUSED: JobSearch on VM-2: working", datetime(2026, 11, 4, 21, 5))
    assert "Chip" in s and "1 to 3 short sentences" in s and "markdown" in s
    assert "FOCUSED: JobSearch on VM-2" in s and "Wednesday 21:05" in s


def test_agent_streams_runs_tools_and_continues():
    llm = FakeLLM([
        {"text": ["Sure, ", "switching."], "tools": [("set_focus", {"session": "Flipopolis"})]},
        {"text": ["Now watching Flipopolis."]},
    ])
    ran = []

    async def tool(name, inp):
        ran.append((name, inp))
        return "Watching Flipopolis on VM-1."

    async def go():
        agent = ChipAgent(ClaudeCfg(), llm, tool)
        return [d async for d in agent.reply("watch flipopolis", [], "ctx")]

    out = asyncio.run(go())
    assert "".join(out) == "Sure, switching. Now watching Flipopolis."
    assert ran == [("set_focus", {"session": "Flipopolis"})]
    second = llm.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["content"][1]["type"] == "tool_use"
    res = second[-1]["content"][0]
    assert res["type"] == "tool_result" and res["tool_use_id"] == second[-2]["content"][1]["id"]
    assert {t["name"] for t in llm.calls[0]["tools"]} == {t["name"] for t in TOOLS}
    assert {"list_sessions", "set_focus", "mute_mic", "find_ranges", "adjust_range"} <= {t["name"] for t in TOOLS}


def test_agent_survives_backend_errors_and_missing_key():
    class Boom(FakeLLM):
        async def stream(self, **kw):
            raise RuntimeError("401")
            yield

    async def go(backend):
        agent = ChipAgent(ClaudeCfg(), backend, None)
        return "".join([d async for d in agent.reply("hi", [], "")])

    assert "can't reach Claude" in asyncio.run(go(Boom([])))
    assert "API key" in asyncio.run(go(None))
