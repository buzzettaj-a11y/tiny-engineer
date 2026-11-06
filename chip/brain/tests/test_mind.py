"""Who answers Joe: the local model first, a spoken hand-off to Claude when it
needs help, and Claude as the fallback when the local model is missing, slow
or broken (ADR 0002)."""

import asyncio

from chipbrain.config import ClaudeCfg, LocalCfg
from chipbrain.mind import Mind
from fakes import FakeLLM


async def _noop_tool(name, inp):
    return "ok"


def _mind(local, claude, run_tool=_noop_tool, timeout=0.5):
    lcfg = LocalCfg(model="qwen3:4b", first_token_timeout_s=timeout)
    return Mind(lcfg, ClaudeCfg(), local, claude, run_tool)


def _say(mind, text="how's it going?", history=None, context="Sessions: none"):
    async def go():
        return "".join([d async for d in mind.reply(text, history or [], context)])
    return asyncio.run(go())


def test_local_model_answers_on_its_own():
    local, claude = FakeLLM([{"text": ["All quiet. ", "Nothing running."]}]), FakeLLM([])
    mind = _mind(local, claude)
    assert _say(mind) == "All quiet. Nothing running."
    assert claude.calls == []
    assert local.calls[0]["model"] == "qwen3:4b"
    assert mind.last_engine == "local"


def test_local_model_hands_off_to_claude_out_loud():
    local = FakeLLM([{"text": ["Let me ask Claude."],
                      "tools": [("ask_claude", {"question": "why are the JobSearch tests failing?"})]}])
    claude = FakeLLM([{"text": ["Two tests time out on the login page."]}])
    mind = _mind(local, claude)
    said = _say(mind, "why are my tests failing?", context="Sessions: JobSearch on VM-2, 2 errors")
    assert said.startswith("Let me ask Claude.")
    assert said.endswith("Two tests time out on the login page.")
    assert any(t["name"] == "ask_claude" for t in local.calls[0]["tools"])
    claude_call = claude.calls[0]
    assert claude_call["messages"][-1]["content"] == "why are my tests failing?"
    assert "JobSearch on VM-2" in claude_call["system"]
    assert "why are the JobSearch tests failing?" in claude_call["system"]
    assert not any(t["name"] == "ask_claude" for t in claude_call["tools"])
    assert mind.last_engine == "hand-off"


def test_hand_off_without_words_still_tells_joe():
    local = FakeLLM([{"tools": [("ask_claude", {"question": "explain the build error"})]}])
    claude = FakeLLM([{"text": ["The build needs Node 22."]}])
    said = _say(_mind(local, claude))
    assert said.startswith("Let me ask Claude.")
    assert said.endswith("The build needs Node 22.")


def test_claude_answers_when_the_local_model_is_slow_to_start():
    local = FakeLLM([{"delay": 1.5, "text": ["Too late."]}])
    claude = FakeLLM([{"text": ["Here's the update."]}])
    mind = _mind(local, claude, timeout=0.3)
    assert _say(mind) == "Here's the update."
    assert mind.last_engine == "fallback"


def test_claude_answers_when_the_local_model_is_down():
    local = FakeLLM([{"error": "connection refused"}])
    claude = FakeLLM([{"text": ["Here's the update."]}])
    mind = _mind(local, claude)
    assert _say(mind) == "Here's the update."
    assert mind.last_engine == "fallback"


def test_claude_alone_when_no_local_model():
    claude = FakeLLM([{"text": ["Hi Joe."]}])
    mind = _mind(None, claude)
    assert _say(mind) == "Hi Joe."
    assert mind.last_engine == "claude"


def test_local_alone_when_claude_is_not_set_up():
    local = FakeLLM([{"tools": [("ask_claude", {"question": "hard one"})]}])
    said = _say(_mind(local, None))
    assert "Claude" in said and "API key" in said


def test_nothing_set_up_says_how_to_fix_it():
    said = _say(_mind(None, None))
    assert "Ollama" in said and "API key" in said


def test_local_model_can_use_chips_tools_and_keep_talking():
    seen = []

    async def run_tool(name, inp):
        seen.append((name, inp))
        return "Remembered."

    local = FakeLLM([{"text": ["Got it."], "tools": [("remember", {"fact": "Joe likes short updates"})]},
                     {"text": ["I'll keep it short."]}])
    said = _say(_mind(local, FakeLLM([]), run_tool=run_tool))
    assert seen == [("remember", {"fact": "Joe likes short updates"})]
    assert said == "Got it. I'll keep it short."
