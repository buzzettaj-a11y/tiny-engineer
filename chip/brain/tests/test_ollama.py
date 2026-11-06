"""The Ollama connection, tested against a fake server speaking Ollama's real
/api/chat streaming format."""

import asyncio
import time

import pytest

from chipbrain.claude import TOOLS
from chipbrain.config import ClaudeCfg, LocalCfg
from chipbrain.mind import Mind
from chipbrain.ollama import OllamaBackend, OllamaError
from fakes import FakeLLM, FakeOllama, ollama_text, ollama_tool


@pytest.fixture
def ollama():
    servers = []

    def make(replies):
        s = FakeOllama(replies).start()
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.stop()


def _cfg(url, **kw):
    return LocalCfg(url=url, model="qwen3:4b", **kw)


def _events(backend, messages=None, tools=TOOLS, system="You are Chip."):
    async def go():
        return [ev async for ev in backend.stream(model="qwen3:4b", system=system,
                                                  messages=messages or [{"role": "user", "content": "hi"}],
                                                  tools=tools, max_tokens=200)]
    return asyncio.run(go())


def test_streams_text_and_sends_the_settings_that_keep_it_fast(ollama):
    srv = ollama([ollama_text("Hi", " Joe.")])
    evs = _events(OllamaBackend(_cfg(srv.url, keep_alive="30m", num_ctx=8192, temperature=0.5)))
    assert [e for e in evs if e[0] == "text"] == [("text", "Hi"), ("text", " Joe.")]
    assert evs[-1] == ("final", [{"type": "text", "text": "Hi Joe."}], "end_turn")
    req = srv.requests[0]
    assert req["path"] == "/api/chat"
    body = req["body"]
    assert body["model"] == "qwen3:4b" and body["stream"] is True
    assert body["keep_alive"] == "30m"
    assert body["think"] is False
    assert body["options"]["num_ctx"] == 8192
    assert body["options"]["num_predict"] == 200
    assert body["options"]["temperature"] == 0.5
    assert body["messages"][0] == {"role": "system", "content": "You are Chip."}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    fn = {t["function"]["name"]: t for t in body["tools"]}["remember"]
    assert fn["type"] == "function"
    assert fn["function"]["parameters"]["required"] == ["fact"]


def test_tool_calls_come_back_as_tool_use(ollama):
    srv = ollama([ollama_tool("set_focus", {"session": "jobsearch"}, text="Watching it.")])
    evs = _events(OllamaBackend(_cfg(srv.url)))
    kind, content, stop = evs[-1]
    assert stop == "tool_use"
    assert content[0] == {"type": "text", "text": "Watching it."}
    use = content[1]
    assert use["type"] == "tool_use" and use["name"] == "set_focus" and use["input"] == {"session": "jobsearch"}
    assert use["id"]


def test_tool_results_in_history_are_sent_the_ollama_way(ollama):
    srv = ollama([ollama_text("Done.")])
    messages = [
        {"role": "user", "content": "watch jobsearch"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Okay."},
            {"type": "tool_use", "id": "t1", "name": "set_focus", "input": {"session": "jobsearch"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Now watching JobSearch."}]},
    ]
    _events(OllamaBackend(_cfg(srv.url)), messages=messages)
    sent = srv.requests[0]["body"]["messages"][1:]
    assert sent[0] == {"role": "user", "content": "watch jobsearch"}
    assert sent[1] == {"role": "assistant", "content": "Okay.",
                       "tool_calls": [{"function": {"name": "set_focus", "arguments": {"session": "jobsearch"}}}]}
    assert sent[2] == {"role": "tool", "content": "Now watching JobSearch.", "tool_name": "set_focus"}


def test_missing_model_says_how_to_get_it(ollama):
    srv = ollama([(404, {"error": "model 'qwen3:4b' not found"})])
    with pytest.raises(OllamaError, match="ollama pull qwen3:4b"):
        _events(OllamaBackend(_cfg(srv.url)))


def test_error_in_the_middle_of_a_stream_is_raised(ollama):
    srv = ollama([[{"message": {"role": "assistant", "content": "I"}, "done": False},
                   {"error": "an error was encountered while running the model"}]])
    with pytest.raises(OllamaError, match="running the model"):
        _events(OllamaBackend(_cfg(srv.url)))


def test_ollama_not_running_fails_fast():
    t0 = time.monotonic()
    with pytest.raises(OllamaError, match="Ollama"):
        _events(OllamaBackend(_cfg("http://127.0.0.1:9")))
    assert time.monotonic() - t0 < 2


def test_mind_with_real_ollama_connection_hands_off_and_falls_back(ollama):
    srv = ollama([ollama_tool("ask_claude", {"question": "why did the build fail?"}, text="Let me ask Claude."),
                  [{"sleep": 1.0}] + ollama_text("too late")])
    claude = FakeLLM([{"text": ["The build needs Node 22."]}, {"text": ["Quiet day."]}])

    async def noop(name, inp):
        return "ok"

    mind = Mind(_cfg(srv.url, first_token_timeout_s=0.4), ClaudeCfg(), OllamaBackend(_cfg(srv.url)), claude, noop)

    async def say(text):
        return "".join([d async for d in mind.reply(text, [], "Sessions: none")])

    assert asyncio.run(say("why did my build fail?")) == "Let me ask Claude. The build needs Node 22."
    assert mind.last_engine == "hand-off"
    assert asyncio.run(say("anything new?")) == "Quiet day."
    assert mind.last_engine == "fallback"


def test_check_reports_whether_ollama_runs_and_has_chips_model():
    from chipbrain.ollama import local_status

    srv = FakeOllama([], models=["qwen3:4b", "llama3.2:3b"]).start()
    try:
        assert "qwen3:4b downloaded" in local_status(_cfg(srv.url))
        missing = local_status(LocalCfg(url=srv.url, model="qwen3:8b"))
        assert "NOT downloaded" in missing and "ollama pull qwen3:8b" in missing
    finally:
        srv.stop()
    assert "not running" in local_status(_cfg("http://127.0.0.1:9"))
    assert "off" in local_status(LocalCfg(enabled=False))


def test_a_slow_tool_call_still_counts_as_answering_once_ollama_starts_streaming(ollama):
    srv = ollama([[{"message": {"role": "assistant", "content": ""}, "done": False}, {"sleep": 0.8}]
                  + ollama_tool("ask_claude", {"question": "why?"})])
    claude = FakeLLM([{"text": ["Because."]}])

    async def noop(name, inp):
        return "ok"

    mind = Mind(_cfg(srv.url, first_token_timeout_s=0.4), ClaudeCfg(), OllamaBackend(_cfg(srv.url)), claude, noop)

    async def say():
        return "".join([d async for d in mind.reply("why?", [], "")])

    assert asyncio.run(say()) == "Let me ask Claude. Because."
    assert mind.last_engine == "hand-off"


def test_warm_up_loads_the_model_and_keeps_it_loaded(ollama):
    srv = ollama([[{"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "load"}]])
    asyncio.run(OllamaBackend(_cfg(srv.url, keep_alive="-1m")).warm_up())
    body = srv.requests[0]["body"]
    assert body["messages"] == [] and body["model"] == "qwen3:4b" and body["keep_alive"] == "-1m"


def test_default_keeps_the_model_loaded_forever():
    assert LocalCfg().keep_alive == "-1m"
