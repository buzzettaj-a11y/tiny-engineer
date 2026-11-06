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


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_after_the_local_model_stalls_claude_answers_straight_away_for_a_while():
    clock = Clock()
    local = FakeLLM([{"delay": 1.0, "text": ["late"]}, {"text": ["Local again."]}])
    claude = FakeLLM([{"text": ["One."]}, {"text": ["Two."]}])
    lcfg = LocalCfg(model="qwen3:4b", first_token_timeout_s=0.2, retry_after_s=300)
    mind = Mind(lcfg, ClaudeCfg(), local, claude, _noop_tool, clock=clock)
    assert _say(mind) == "One."
    assert _say(mind) == "Two."  # no 0.2 s wait on the local model this time
    assert len(local.calls) == 1 and mind.last_engine == "fallback"
    clock.t += 301
    assert _say(mind) == "Local again."
    assert mind.last_engine == "local"


def test_local_model_failing_mid_answer_hands_over_to_claude_out_loud():
    class Breaks(FakeLLM):
        async def stream(self, **kw):
            self.calls.append(kw)
            yield ("text", "Your build is")
            raise ConnectionError("model crashed")

    claude = FakeLLM([{"text": ["The build failed on a missing import."]}])
    said = _say(_mind(Breaks([]), claude))
    assert said.startswith("Your build is")
    assert "Let me ask Claude." in said
    assert said.endswith("The build failed on a missing import.")


def test_claude_is_told_what_the_local_model_already_did():
    seen = []

    async def run_tool(name, inp):
        seen.append(name)
        return "Now watching JobSearch."

    local = FakeLLM([{"tools": [("set_focus", {"session": "jobsearch"})]}, {"error": "model crashed"}])
    claude = FakeLLM([{"text": ["I'm watching JobSearch."]}])
    _say(_mind(local, claude, run_tool=run_tool))
    assert seen == ["set_focus"]
    note = claude.calls[0]["system"]
    assert "already did" in note and "set_focus" in note and "Now watching JobSearch." in note


def test_only_claude_gets_the_tools_that_move_hardware():
    local, claude = FakeLLM([{"tools": [("ask_claude", {"question": "find my ranges"})]}]), FakeLLM([{"text": ["On it."]}])
    _say(_mind(local, claude), "find your ranges")
    local_tools = {t["name"] for t in local.calls[0]["tools"]}
    claude_tools = {t["name"] for t in claude.calls[0]["tools"]}
    assert {"find_ranges", "adjust_range"} & local_tools == set()
    assert {"find_ranges", "adjust_range"} <= claude_tools
    assert {"remember", "set_focus", "ask_claude"} <= local_tools


def test_local_prompt_stays_identical_between_turns_so_ollama_can_reuse_it():
    local = FakeLLM([{"text": ["A."]}, {"text": ["B."]}])
    mind = _mind(local, FakeLLM([]))

    async def go():
        for ctx in ("Sessions: JobSearch busy", "Sessions: all quiet"):
            "".join([d async for d in mind.reply("status?", [], ctx, taught="Joe likes short answers")])

    asyncio.run(go())
    first, second = local.calls
    assert first["system"] == second["system"]
    assert "Joe likes short answers" in first["system"]
    assert "Sessions: all quiet" in second["messages"][-1]["content"]
    assert second["messages"][-1]["content"].rstrip().endswith("status?")


def test_claude_still_gets_the_notes_and_teaching_in_its_system_prompt():
    claude = FakeLLM([{"text": ["Hi."]}])
    mind = _mind(None, claude)

    async def go():
        return "".join([d async for d in mind.reply("hi", [], "Sessions: JobSearch busy", taught="Joe likes tea")])

    asyncio.run(go())
    assert "Sessions: JobSearch busy" in claude.calls[0]["system"]
    assert "Joe likes tea" in claude.calls[0]["system"]
    assert claude.calls[0]["messages"][-1]["content"] == "hi"
