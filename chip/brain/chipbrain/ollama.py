"""Chip's local model, through Ollama's native chat API (ADR 0002).

Speaks ``POST /api/chat`` with streaming (newline-delimited JSON), as
documented in ollama/ollama docs/api.md ("Generate a chat completion",
"Chat request (Streaming with tools)", "With history, with tools") and
docs/api/errors.mdx (errors mid-stream arrive as ``{"error": ...}`` lines).

The native API is used rather than Ollama's Anthropic-compatible one because
only it takes ``keep_alive``, ``options.num_ctx`` and ``think``, which decide
whether replies feel instant. ``think: false`` is safe on models that can't
think: Ollama only rejects ``think: true`` for those (server/routes.go).

The brain talks to every model in Anthropic's message shape (see claude.py);
this module converts to and from Ollama's shape. Standard library only.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import threading
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

from .claude import LLMBackend
from .config import LocalCfg

log = logging.getLogger("chip.ollama")


class OllamaError(Exception):
    pass


def to_ollama_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t.get("input_schema", {"type": "object", "properties": {}})}}
            for t in tools]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def to_ollama_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    names: dict[str, str] = {}  # tool_use id -> tool name (Ollama tool results carry the name)
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = content or []
        if role == "assistant":
            msg: dict = {"role": "assistant", "content": _text_of(blocks)}
            calls = []
            for b in blocks:
                if b.get("type") == "tool_use":
                    names[b.get("id", "")] = b["name"]
                    calls.append({"function": {"name": b["name"], "arguments": b.get("input") or {}}})
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        text = _text_of(blocks)
        if text:
            out.append({"role": role, "content": text})
        for b in blocks:
            if b.get("type") == "tool_result":
                out.append({"role": "tool", "content": _text_of(b.get("content", "")),
                            "tool_name": names.get(b.get("tool_use_id", ""), "")})
    return out


class OllamaBackend(LLMBackend):
    def __init__(self, cfg: LocalCfg, connect_timeout_s: float = 1.5, read_timeout_s: float = 60.0):
        self.cfg = cfg
        u = urlsplit(cfg.url)
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if u.scheme == "https" else 11434)
        self.https = u.scheme == "https"
        self.connect_timeout_s = connect_timeout_s
        self.read_timeout_s = read_timeout_s

    def _body(self, model: str, system: str, messages: list[dict], tools: list[dict], max_tokens: int) -> dict:
        body = {
            "model": model,
            "messages": to_ollama_messages(system, messages),
            "stream": True,
            "keep_alive": self.cfg.keep_alive,
            "think": self.cfg.think,
            "options": {"num_ctx": self.cfg.num_ctx, "num_predict": max_tokens,
                        "temperature": self.cfg.temperature},
        }
        if tools:
            body["tools"] = to_ollama_tools(tools)
        return body

    async def stream(self, *, model, system, messages, tools, max_tokens) -> AsyncIterator[tuple]:
        data = json.dumps(self._body(model, system, messages, tools, max_tokens)).encode()
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        holder: dict = {}

        def put(item):
            loop.call_soon_threadsafe(q.put_nowait, item)

        def worker():
            try:
                cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
                conn = cls(self.host, self.port, timeout=self.connect_timeout_s)
                holder["conn"] = conn
                conn.connect()
                conn.sock.settimeout(self.read_timeout_s)
                conn.request("POST", "/api/chat", body=data, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                if resp.status != 200:
                    put(("status", (resp.status, resp.read())))
                    return
                for line in resp:
                    if stop.is_set():
                        return
                    if line.strip():
                        put(("line", line))
                put(("end", None))
            except Exception as e:  # noqa: BLE001 - every failure goes back to the caller
                if not stop.is_set():
                    put(("exc", e))

        threading.Thread(target=worker, name="ollama", daemon=True).start()
        text: list[str] = []
        calls: list[dict] = []
        try:
            while True:
                kind, val = await q.get()
                if kind == "exc":
                    raise OllamaError(f"Ollama isn't reachable at {self.cfg.url} ({val})")
                if kind == "status":
                    raise self._status_error(model, *val)
                if kind == "end":
                    break
                obj = json.loads(val)
                if obj.get("error"):
                    raise OllamaError(str(obj["error"]))
                msg = obj.get("message") or {}
                if msg.get("content"):
                    text.append(msg["content"])
                    yield ("text", msg["content"])
                for c in msg.get("tool_calls") or []:
                    fn = c.get("function") or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):  # some models send a JSON string
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    calls.append({"type": "tool_use", "id": "call_" + uuid.uuid4().hex[:12],
                                  "name": fn.get("name", ""), "input": args})
                if obj.get("done"):
                    break
        finally:
            stop.set()
            conn = holder.get("conn")
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        content: list[dict] = []
        if text:
            content.append({"type": "text", "text": "".join(text)})
        content += calls
        yield ("final", content, "tool_use" if calls else "end_turn")

    def _status_error(self, model: str, status: int, raw: bytes) -> OllamaError:
        try:
            msg = str(json.loads(raw).get("error", ""))
        except ValueError:
            msg = raw.decode("utf-8", "replace")[:200]
        if status == 404 and "not found" in msg:
            return OllamaError(f"the model {model} isn't downloaded yet. Run: ollama pull {model}")
        return OllamaError(f"Ollama answered {status}: {msg}")


def local_status(cfg: LocalCfg, timeout_s: float = 2.0) -> str:
    """One line for `chipbrain check`: is Ollama running, is Chip's model downloaded?
    Uses GET /api/tags ("List Local Models" in docs/api.md)."""
    if not cfg.enabled:
        return "off ([local] enabled = false): Claude answers everything"
    u = urlsplit(cfg.url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    try:
        conn = cls(u.hostname or "127.0.0.1", u.port or 11434, timeout=timeout_s)
        conn.request("GET", "/api/tags")
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"{}")
        conn.close()
    except (OSError, ValueError) as e:
        return f"not running at {cfg.url} ({e}): Claude answers instead. Start Ollama."
    names = {m.get("name") or m.get("model") for m in data.get("models", [])}
    want = cfg.model if ":" in cfg.model else cfg.model + ":latest"
    if cfg.model in names or want in names:
        return f"running at {cfg.url}, {cfg.model} downloaded"
    return f"running at {cfg.url}, but {cfg.model} is NOT downloaded. Run: ollama pull {cfg.model}"
