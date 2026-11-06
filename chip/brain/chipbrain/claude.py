"""Chip's mind: Claude via the Anthropic Python SDK, streamed, with tools.

Verified against anthropic-sdk-python: ``AsyncAnthropic().messages.stream(...)``
is an async context manager; iterating it yields events, ``event.type ==
"text"`` carries ``event.text`` (the delta); ``await stream.get_final_message()``
returns content blocks (``tool_use`` blocks have ``id``, ``name``, ``input``)
and ``stop_reason`` (``"tool_use"`` when tools must run).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable

from .config import ClaudeCfg

log = logging.getLogger("chip.claude")

TOOLS: list[dict] = [
    {
        "name": "list_sessions",
        "description": "List Joe's Claude Code sessions (label, status, idle time). Use when he asks what's running.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "session_detail",
        "description": "Recent prompts, tools, edited files and errors of one session.",
        "input_schema": {
            "type": "object",
            "properties": {"session": {"type": "string", "description": "Label, folder name, host or id."}},
            "required": ["session"],
        },
    },
    {
        "name": "set_focus",
        "description": (
            "Choose which session Chip watches (its animations follow that session). "
            "Pass 'auto' to follow whichever session is most active."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"session": {"type": "string", "description": "Label, folder name or id, or 'auto'."}},
            "required": ["session"],
        },
    },
    {
        "name": "mute_mic",
        "description": (
            "Mute or unmute Chip's microphone. While muted Chip hears nothing, so Joe unmutes with the "
            "button on the robot or the web panel. Confirm before muting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"muted": {"type": "boolean"}},
            "required": ["muted"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Save something Joe teaches you so you remember it from now on (a preference, a name, a rule). "
            "Use when he says remember, from now on, always or never. Keep it to one short sentence about Joe."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "One short sentence, e.g. 'Joe likes one-sentence updates'."}},
            "required": ["fact"],
        },
    },
    {
        "name": "forget",
        "description": "Remove things you were taught that match these words. Use when Joe says forget or stop doing something.",
        "input_schema": {
            "type": "object",
            "properties": {"about": {"type": "string", "description": "Words from the thing to forget."}},
            "required": ["about"],
        },
    },
    {
        "name": "find_ranges",
        "description": (
            "Slowly explore each joint's movement range until it bumps, then save safer limits. "
            "Takes a minute or two and Chip narrates it. Only when Joe asks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "joints": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["head", "neck", "left", "right", "body"]},
                    "description": "Joints to explore; omit for all.",
                }
            },
        },
    },
    {
        "name": "adjust_range",
        "description": (
            "Tighten or loosen one end of a joint's saved range. Loosening never goes past the last "
            "explored safe value or the hard cap. Only when Joe asks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "joint": {"type": "string", "enum": ["head", "neck", "left", "right", "body"]},
                "dir": {"type": "string", "enum": ["min", "max"], "description": "Which end of the range."},
                "action": {"type": "string", "enum": ["tighten", "loosen"]},
                "degrees": {"type": "number", "minimum": 1, "maximum": 20},
            },
            "required": ["joint", "dir", "action", "degrees"],
        },
    },
]

PERSONA = """You are Chip, a small desk robot (a Tiny Engineer) on {owner}'s desk. You keep an eye on {owner}'s Claude Code sessions, which run on his Windows VMs, and you chat with him by voice.

Everything you write is spoken aloud by a tiny speaker. Reply in 1 to 3 short sentences of plain spoken English. Never use markdown, lists, code, emoji, URLs or file paths; say project and file names naturally. Lead with the answer. {owner} has ADHD: be brief, concrete and warm, and when it helps, name the one next thing to do. Only report what the session notes below say; if you don't know, say so.

Tools let you change which session you watch, list sessions, mute your mic, remember or forget what Joe teaches you, and find or adjust your joint ranges. Use range and mute tools only when {owner} asks. After a tool, say what you did in a few words."""

WORK_WORDS = {
    "session", "sessions", "claude", "task", "tasks", "error", "errors", "focus", "range", "ranges", "build",
    "test", "tests", "failing", "failed", "fail", "code", "file", "files", "working", "doing", "status",
    "vm", "bug", "commit", "permission", "waiting", "switch", "watch", "mute", "explore", "limit", "deploy",
    "done", "finished", "progress", "stuck", "why",
}


def choose_model(cfg: ClaudeCfg, text: str, session_names: list[str]) -> str:
    """fast_model for short casual chat, main model for anything about work."""
    if not cfg.fast_model:
        return cfg.model
    toks = re.findall(r"[a-z0-9']+", text.lower())
    if len(toks) > 14 or any(t in WORK_WORDS for t in toks):
        return cfg.model
    flat = "".join(toks)
    for name in session_names:
        n = re.sub(r"[^a-z0-9]", "", name.lower())
        if n and n in flat:
            return cfg.model
    return cfg.fast_model


def build_system(cfg: ClaudeCfg, context: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return (
        PERSONA.format(owner=cfg.owner_name)
        + f"\n\nNow: {now.strftime('%A %H:%M')}.\n\n{context.strip()}"
    )


class LLMBackend:
    """Streams one model call. Yields ("text", delta) events, then exactly one
    ("final", content_blocks_as_dicts, stop_reason)."""

    async def stream(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                     max_tokens: int) -> AsyncIterator[tuple]:
        raise NotImplementedError
        yield  # pragma: no cover


class AnthropicBackend(LLMBackend):
    def __init__(self):
        from anthropic import AsyncAnthropic  # lazy; reads ANTHROPIC_API_KEY

        self.client = AsyncAnthropic()

    async def stream(self, *, model, system, messages, tools, max_tokens):
        async with self.client.messages.stream(
            model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    yield ("text", event.text)
            final = await stream.get_final_message()
        yield ("final", [_block_dict(b) for b in final.content], final.stop_reason)


def _block_dict(b: Any) -> dict:
    t = getattr(b, "type", None)
    if t == "text":
        return {"type": "text", "text": b.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b)


ToolRunner = Callable[[str, dict], Awaitable[str]]


class HandOff(Exception):
    """The model asked for someone smarter (see Mind)."""

    def __init__(self, question: str, spoke: str):
        super().__init__(question)
        self.question = question
        self.spoke = spoke


class ChipAgent:
    """One model, its tools and the tool loop. Yields spoken text deltas.

    ``model``: fixed model name; empty = choose per message (Claude's fast_model).
    ``handoff``: tool name that ends this agent's turn by raising HandOff.
    ``fail_quietly``: on backend errors say sorry (Claude) or raise (local, so
    the caller can fall back).
    """

    def __init__(self, cfg: ClaudeCfg, backend: LLMBackend | None, run_tool: ToolRunner, *,
                 tools: list[dict] | None = None, model: str = "", handoff: str = "",
                 fail_quietly: bool = True, system_extra: str = ""):
        self.cfg = cfg
        self.backend = backend
        self.run_tool = run_tool
        self.tools = TOOLS if tools is None else tools
        self.model = model
        self.handoff = handoff
        self.fail_quietly = fail_quietly
        self.system_extra = system_extra
        self.last_model = ""

    async def reply(self, user_text: str, history: list[dict], context: str,
                    session_names: list[str] | None = None, now: datetime | None = None) -> AsyncIterator[str]:
        """Yield spoken text deltas. Tool calls run between model rounds.

        An empty-string delta means "the model has started answering" (it may
        be calling a tool first); callers that only speak can ignore it.
        """
        if self.backend is None:
            yield "My brain isn't connected to Claude yet. Set the Anthropic API key and restart me."
            return
        model = self.model or choose_model(self.cfg, user_text, session_names or [])
        self.last_model = model
        system = build_system(self.cfg, context, now)
        if self.system_extra:
            system = system.replace("\n\nNow:", "\n\n" + self.system_extra + "\n\nNow:", 1)
        messages = list(history) + [{"role": "user", "content": user_text}]
        spoke = False
        said: list[str] = []
        try:
            for round_no in range(self.cfg.max_tool_rounds + 1):
                content: list[dict] = []
                stop_reason = None
                if round_no and spoke:
                    yield " "
                started = False
                async for ev in self.backend.stream(model=model, system=system, messages=messages,
                                                    tools=self.tools, max_tokens=self.cfg.max_tokens):
                    if not started:
                        started = True
                        yield ""
                    if ev[0] == "text":
                        spoke = True
                        said.append(ev[1])
                        yield ev[1]
                    elif ev[0] == "final":
                        content, stop_reason = ev[1], ev[2]
                if stop_reason != "tool_use":
                    return
                tool_uses = [b for b in content if b.get("type") == "tool_use"]
                if not tool_uses:
                    return
                for tu in tool_uses:
                    if self.handoff and tu["name"] == self.handoff:
                        raise HandOff(str((tu.get("input") or {}).get("question", "")), "".join(said))
                messages.append({"role": "assistant", "content": content})
                results = []
                for tu in tool_uses:
                    try:
                        out = await self.run_tool(tu["name"], tu.get("input") or {})
                        is_error = False
                    except Exception as e:  # tool bugs must not kill the reply
                        log.exception("tool %s failed", tu["name"])
                        out, is_error = f"Tool failed: {e}", True
                    log.info("tool %s(%s) -> %s", tu["name"], tu.get("input"), out)
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": out,
                                    "is_error": is_error})
                messages.append({"role": "user", "content": results})
        except HandOff:
            raise
        except Exception as e:
            if not self.fail_quietly:
                raise
            log.error("Claude call failed: %s", e)
            if not spoke:
                yield "Sorry, I can't reach Claude right now."
