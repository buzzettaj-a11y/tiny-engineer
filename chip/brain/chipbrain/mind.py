"""Who answers Joe (ADR 0002).

The local model (Ollama on the home PC) answers first. When it needs help it
calls ``ask_claude``: Chip says "Let me ask Claude." and Claude answers the
same request with the same context. If the local model is missing, too slow to
start answering, or fails, Claude answers instead (fallback), so Chip never
goes silent. After a fallback the local model gets a rest (``retry_after_s``)
so a slow PC doesn't add a wait to every turn.

The local model only gets tools that don't move hardware; joint ranges go
through Claude (small models are unreliable at tool calls).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from datetime import datetime
from typing import AsyncIterator, Callable

from .claude import TOOLS, ChipAgent, HandOff, LLMBackend, ToolRunner
from .config import ClaudeCfg, LocalCfg

log = logging.getLogger("chip.mind")

CLAUDE_ONLY_TOOLS = {"find_ranges", "adjust_range"}  # these move the robot's joints

ASK_CLAUDE = {
    "name": "ask_claude",
    "description": (
        "Hand {owner}'s request to Claude, a much smarter AI, who answers him directly. Use it for anything "
        "that needs careful reasoning, explaining why something failed in his code or sessions, changing "
        "Chip's joint ranges, or when you are not sure. Say 'Let me ask Claude.' first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "What Claude should figure out, in one line."}},
        "required": ["question"],
    },
}

LOCAL_EXTRA = (
    "You run on {owner}'s own PC and answer quickly. You can hand a request to Claude with ask_claude. "
    "Do that instead of guessing whenever it needs real reasoning, digging into his code or sessions, "
    "changing your joint ranges, or you aren't sure."
)

HAND_OFF_LINE = "Let me ask Claude."
NO_CLAUDE = "I'd need Claude for that, but Claude isn't set up. Add the Anthropic API key and restart me."
NOTHING = ("My brain isn't connected to an AI yet. Start Ollama on this PC, or set the Anthropic API key, "
           "then restart me.")
LOCAL_DOWN_NO_CLAUDE = "My local brain isn't answering and Claude isn't set up. Check that Ollama is running."
LOST_NO_CLAUDE = " Sorry, I lost my train of thought."


def local_tools(owner: str) -> list[dict]:
    ask = dict(ASK_CLAUDE, description=ASK_CLAUDE["description"].format(owner=owner))
    return [t for t in TOOLS if t["name"] not in CLAUDE_ONLY_TOOLS] + [ask]


def already_done_note(tools_run: list[tuple[str, dict, str]]) -> str:
    if not tools_run:
        return ""
    done = "; ".join(f"{name}({json.dumps(inp)}) -> {out}" for name, inp, out in tools_run)
    return f"Your quick local helper already did this for the request (don't do it again): {done}"


class Mind:
    def __init__(self, local_cfg: LocalCfg, claude_cfg: ClaudeCfg, local: LLMBackend | None,
                 claude: LLMBackend | None, run_tool: ToolRunner, *, clock: Callable[[], float] = time.monotonic):
        self.local_cfg = local_cfg
        self.clock = clock
        self.local_backend = local if local_cfg.enabled else None
        self.local = None
        if self.local_backend is not None:
            cfg = dataclasses.replace(claude_cfg, model=local_cfg.model, fast_model="",
                                      max_tokens=local_cfg.max_tokens)
            owner = claude_cfg.owner_name
            self.local = ChipAgent(cfg, self.local_backend, run_tool, tools=local_tools(owner),
                                   model=local_cfg.model, handoff="ask_claude", raise_errors=True,
                                   system_extra=LOCAL_EXTRA.format(owner=owner), context_in_user_turn=True)
        self.claude = ChipAgent(claude_cfg, claude, run_tool) if claude is not None else None
        self.local_resting_until = 0.0
        self.last_engine = ""

    @property
    def last_model(self) -> str:
        if self.last_engine == "local" and self.local:
            return self.local.last_model
        return self.claude.last_model if self.claude else ""

    async def warm_up(self) -> None:
        """Load the local model now so the first "Hey Chip" doesn't wait for it."""
        warm = getattr(self.local_backend, "warm_up", None)
        if warm is None:
            return
        try:
            await warm()
            log.info("local model %s loaded", self.local_cfg.model)
        except Exception as e:  # noqa: BLE001 - Claude covers for it
            log.warning("couldn't load the local model yet: %s", e)

    async def reply(self, user_text: str, history: list[dict], context: str,
                    session_names: list[str] | None = None, now: datetime | None = None,
                    taught: str = "") -> AsyncIterator[str]:
        args = (user_text, history, context, session_names, now, taught)
        if self.local is not None:
            self.local.tools_run = []  # only this request's tools count for the "already did" note
        if self.local is None or self.clock() < self.local_resting_until:
            if self.claude is None:
                self.last_engine = "none"
                yield NOTHING if self.local is None else LOCAL_DOWN_NO_CLAUDE
                return
            self.last_engine = "claude" if self.local is None else "fallback"
            async for d in self._claude(*args):
                yield d
            return

        self.last_engine = "local"
        gen = self.local.reply(user_text, history, context, session_names, now, taught)
        spoke = False
        try:
            try:
                first = await asyncio.wait_for(anext(gen), self.local_cfg.first_token_timeout_s)
            except StopAsyncIteration:
                return
            if first:
                spoke = True
                yield first
            async for d in gen:
                if d:
                    spoke = True
                    yield d
        except HandOff as h:
            self.last_engine = "hand-off"
            log.info("hand-off to Claude: %s", h.question)
            if "claude" not in h.spoke.lower():
                yield (" " if h.spoke.strip() else "") + HAND_OFF_LINE
            if self.claude is None:
                yield " " + NO_CLAUDE
                return
            yield " "
            note = f"Your quick local helper passed this request to you and asked: {h.question}"
            async for d in self._claude(*args, note=note):
                yield d
        except Exception as e:  # includes the first-answer timeout
            await gen.aclose()
            why = "too slow to start" if isinstance(e, asyncio.TimeoutError) else (str(e) or type(e).__name__)
            log.warning("local model %s; Claude answers instead (local rests %.0f s)", why,
                        self.local_cfg.retry_after_s)
            self.local_resting_until = self.clock() + self.local_cfg.retry_after_s
            self.last_engine = "fallback"
            if self.claude is None:
                yield LOST_NO_CLAUDE if spoke else LOCAL_DOWN_NO_CLAUDE
                return
            if spoke:
                yield " " + HAND_OFF_LINE + " "
            async for d in self._claude(*args):
                yield d

    async def _claude(self, user_text, history, context, session_names, now, taught,
                      note: str = "") -> AsyncIterator[str]:
        notes = [n for n in (note, already_done_note(self.local.tools_run if self.local else [])) if n]
        if notes:
            context = context + "\n\n" + "\n".join(notes)
        async for d in self.claude.reply(user_text, history, context, session_names, now, taught):
            if d:
                yield d
