"""Teaching: "Chip, remember ..." is kept in Chip's memory, survives a restart,
and every model sees it on later turns. "Forget" takes it out again."""

import asyncio

from chipbrain.app import Brain
from fakes import FakeLLM, FakeTranscriber, FakeTTS
from test_brain_e2e import make_cfg


def _brain(robot, tmp_path, llm):
    return Brain(make_cfg(robot, tmp_path), transcriber=FakeTranscriber(lambda s, i: ""), tts=FakeTTS(),
                 llm=llm, wake_detector=None)


def test_taught_things_are_remembered_across_turns_and_restarts(robot, tmp_path):
    llm = FakeLLM([
        {"text": ["Got it."], "tools": [("remember", {"fact": "Joe likes updates in one sentence"})]},
        {"text": ["Noted."]},
        {"text": ["Hi Joe."]},
    ])

    async def teach():
        brain = _brain(robot, tmp_path, llm)
        await brain.start(udp=False)
        try:
            await brain.respond("Chip, remember I like updates in one sentence")
            await brain.respond("how's it going")
        finally:
            await brain.stop()

    asyncio.run(teach())
    assert "Joe likes updates in one sentence" in llm.calls[-1]["system"]
    assert any(t["name"] == "remember" for t in llm.calls[0]["tools"])

    later = FakeLLM([{"text": ["Morning."]}])

    async def restart():
        brain = _brain(robot, tmp_path, later)
        await brain.start(udp=False)
        try:
            await brain.respond("morning chip")
        finally:
            await brain.stop()

    asyncio.run(restart())
    assert "Joe likes updates in one sentence" in later.calls[0]["system"]


def test_forget_removes_a_taught_thing(robot, tmp_path):
    llm = FakeLLM([
        {"text": ["Okay."], "tools": [("remember", {"fact": "Joe's standup is at 9:30"})]},
        {"text": ["Okay."], "tools": [("remember", {"fact": "Call the head tilt 'nodding'"})]},
        {"text": ["Done."], "tools": [("forget", {"about": "standup"})]},
        {"text": ["Hi."]},
    ])

    async def go():
        brain = _brain(robot, tmp_path, llm)
        await brain.start(udp=False)
        try:
            for said in ("remember my standup is at 9:30", "remember to call the head tilt nodding",
                         "forget about my standup", "hi"):
                await brain.respond(said)
        finally:
            await brain.stop()

    asyncio.run(go())
    last = llm.calls[-1]["system"]
    assert "standup" not in last
    assert "nodding" in last


def test_memory_has_a_size_limit_and_ignores_duplicates(robot, tmp_path):
    cfg = make_cfg(robot, tmp_path)
    cfg.brain.memory_max_items = 2
    brain = Brain(cfg, transcriber=FakeTranscriber(lambda s, i: ""), tts=FakeTTS(), llm=None, wake_detector=None)

    async def go():
        assert "Remembered" in await brain.run_tool("remember", {"fact": "one"})
        assert "already" in await brain.run_tool("remember", {"fact": "One"})
        await brain.run_tool("remember", {"fact": "two"})
        out = await brain.run_tool("remember", {"fact": "three"})
        assert "full" in out
        assert "Nothing" in await brain.run_tool("forget", {"about": "zebra"})

    asyncio.run(go())
