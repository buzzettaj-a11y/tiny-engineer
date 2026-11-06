"""Who answers Joe (ADR 0002).

The local model (Ollama on the home PC) answers first. When it needs help it
calls ``ask_claude``: Chip says "Let me ask Claude." and Claude answers the
same request with the same context. If the local model is missing, too slow to
start answering, or fails before saying anything, Claude answers instead
(fallback), so Chip never goes silent.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime
from typing import AsyncIterator

from .claude import TOOLS, ChipAgent, HandOff, LLMBackend, ToolRunner
from .config import ClaudeCfg, LocalCfg

log = logging.getLogger("chip.mind")

ASK_CLAUDE = {
    "name": "ask_claude",
    "description": (
        "Hand Joe's request to Claude, a much smarter AI, who answers him directly. Use it for anything "
        "that needs careful reasoning, explaining why something failed in his code or sessions, or when "
        "you are not sure. Say 'Let me ask Claude.' first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "What Claude should figure out, in one line."}},
        "required": ["question"],
    },
}

LOCAL_EXTRA = (
    "You run on Joe's own PC and answer quickly. You can hand a request to Claude with ask_claude. "
    "Do that instead of guessing whenever it needs real reasoning, digging into his code or sessions, "
    "or you aren't sure."
)

HAND_OFF_LINE = "Let me ask Claude."
NO_CLAUDE = "I'd need Claude for that, but Claude isn't set up. Add the Anthropic API key and restart me."
NOTHING = ("My brain isn't connected to an AI yet. Start Ollama on this PC, or set the Anthropic API key, "
           "then restart me.")
LOCAL_DOWN_NO_CLAUDE = "My local brain isn't answering and Claude isn't set up. Check that Ollama is running."
LOST = " Sorry, I lost my train of thought."


class Mind:
    def __init__(self, local_cfg: LocalCfg, claude_cfg: ClaudeCfg, local: LLMBackend | None,
                 claude: LLMBackend | None, run_tool: ToolRunner):
        self.local_cfg = local_cfg
        self.local = None
        if local is not None and local_cfg.enabled:
            cfg = dataclasses.replace(claude_cfg, model=local_cfg.model, fast_model="",
                                      max_tokens=local_cfg.max_tokens)
            self.local = ChipAgent(cfg, local, run_tool, tools=TOOLS + [ASK_CLAUDE], model=local_cfg.model,
                                   handoff="ask_claude", fail_quietly=False, system_extra=LOCAL_EXTRA)
        self.claude = ChipAgent(claude_cfg, claude, run_tool) if claude is not None else None
        self.last_engine = ""

    @property
    def last_model(self) -> str:
        if self.last_engine == "local" and self.local:
            return self.local.last_model
        return self.claude.last_model if self.claude else ""

    async def reply(self, user_text: str, history: list[dict], context: str,
                    session_names: list[str] | None = None, now: datetime | None = None) -> AsyncIterator[str]:
        if self.local is None:
            if self.claude is None:
                self.last_engine = "none"
                yield NOTHING
                return
            self.last_engine = "claude"
            async for d in self._claude(user_text, history, context, session_names, now):
                yield d
            return

        self.last_engine = "local"
        gen = self.local.reply(user_text, history, context, session_names, now)
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
            async for d in self._claude(user_text, history, context + "\n\n" + note, session_names, now):
                yield d
        except (asyncio.TimeoutError, Exception) as e:
            await gen.aclose()
            if spoke:
                log.warning("local model failed mid-answer: %s", e)
                yield LOST
                return
            why = "too slow to start" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
            log.warning("local model %s; Claude answers instead", why)
            self.last_engine = "fallback"
            if self.claude is None:
                yield LOCAL_DOWN_NO_CLAUDE
                return
            async for d in self._claude(user_text, history, context, session_names, now):
                yield d

    async def _claude(self, user_text, history, context, session_names, now) -> AsyncIterator[str]:
        async for d in self.claude.reply(user_text, history, context, session_names, now):
            if d:
                yield d
