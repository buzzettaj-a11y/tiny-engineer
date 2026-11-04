"""Tiny asyncio HTTP/1.1 JSON server (stdlib only). One request per
connection; enough for hook intake and a few debug routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("chip.http")

Handler = Callable[[str, str, dict, Any], Awaitable[tuple[int, Any]]]
MAX_BODY = 1_000_000
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
           413: "Payload Too Large", 500: "Internal Server Error"}


class JsonHttpServer:
    def __init__(self, handler: Handler, host: str, port: int):
        self.handler = handler
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._conn, self.host, self.port)
        sock = self._server.sockets[0].getsockname()
        self.port = sock[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, payload = 500, {"ok": False, "error": "internal"}
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            parts = line.decode("latin-1").split()
            if len(parts) < 2:
                payload = None
                return
            method, target = parts[0].upper(), parts[1]
            headers: dict[str, str] = {}
            while True:
                h = await asyncio.wait_for(reader.readline(), 10)
                if h in (b"\r\n", b"\n", b""):
                    break
                k, _, v = h.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            try:
                n = int(headers.get("content-length") or 0)
            except ValueError:
                n = -1
            if n < 0:
                status, payload = 400, {"ok": False, "error": "bad Content-Length"}
            elif n > MAX_BODY:
                status, payload = 413, {"ok": False, "error": "body too large"}
            else:
                raw = await asyncio.wait_for(reader.readexactly(n), 10) if n else b""
                body: Any = None
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        status, payload = 400, {"ok": False, "error": "body must be JSON"}
                        raw = None
                if raw is not None:
                    path = target.split("?", 1)[0]
                    status, payload = await self.handler(method, path, headers, body)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            payload = None
            return
        except Exception:
            log.exception("HTTP handler crashed")
            status, payload = 500, {"ok": False, "error": "internal"}
        finally:
            try:
                if payload is not None and not writer.is_closing():
                    data = json.dumps(payload, default=str).encode()
                    writer.write(
                        f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}\r\n"
                        f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                        "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n".encode() + data
                    )
                    await writer.drain()
            except Exception:
                pass
            writer.close()
