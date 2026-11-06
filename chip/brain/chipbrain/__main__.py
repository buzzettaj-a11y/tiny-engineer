"""Command line.

  python -m chipbrain                 run the brain (reads chip.toml here)
  python -m chipbrain check           show what is installed / configured
  python -m chipbrain point-robot     tell the robot to stream its mic to this PC
  python -m chipbrain say "Hello"     speak one line through the robot (TTS test)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import socket
import sys
import urllib.parse
from pathlib import Path

from . import __version__
from .config import Config, load


def _setup_logging(level: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows consoles and "→"
        except Exception:
            pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def _find_config(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    for p in (Path.cwd() / "chip.toml", Path(__file__).resolve().parent.parent / "chip.toml"):
        if p.exists():
            return p
    return Path.cwd() / "chip.toml"


async def _run(cfg: Config) -> None:
    from .app import Brain

    brain = Brain(cfg)
    await brain.start()
    logging.getLogger("chip").info("Chip brain %s ready. Say \"Hey Chip\". Ctrl+C to stop.", __version__)
    try:
        await asyncio.Event().wait()
    finally:
        await brain.stop()


def cmd_check(cfg: Config, path: Path) -> int:
    def has(mod: str) -> str:
        return "yes" if importlib.util.find_spec(mod) else "NO"

    print(f"config file      : {path} ({'found' if path.exists() else 'missing, using defaults'})")
    print(f"robot            : {cfg.robot.url}")
    from .ollama import local_status

    print(f"local AI (Ollama): {local_status(cfg.local)}")
    print(f"ANTHROPIC_API_KEY: {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'NOT SET'}")
    print(f"anthropic        : {has('anthropic')}")
    print(f"faster_whisper   : {has('faster_whisper')}   model {cfg.stt.model}")
    print(f"webrtcvad        : {has('webrtcvad')} (optional)")
    print(f"piper            : {has('piper')}  voice {cfg.tts.piper_model or '(none)'}")
    print(f"comtypes/pyttsx3 : {has('comtypes')}/{has('pyttsx3')} (Windows voices)")
    print(f"openwakeword     : {has('openwakeword')} (optional)")
    try:
        status, body = _robot_request(cfg, "GET", "/health")
        print(f"robot /health    : HTTP {status} {body[:160]}")
    except OSError as e:
        print(f"robot /health    : unreachable ({e})")
    return 0


def _robot_request(cfg: Config, method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
    from .robot import Transport

    headers = {"Authorization": f"Bearer {cfg.robot.token}"} if cfg.robot.token else {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    status, raw = Transport().request(method, cfg.robot.url.rstrip("/") + path, body, headers, 5.0)
    return status, raw.decode("utf-8", "replace")


def local_ip_towards(host: str, port: int = 80) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, port))  # no packet is sent; just picks the route
        return s.getsockname()[0]
    finally:
        s.close()


def cmd_point_robot(cfg: Config, ip: str | None) -> int:
    host = urllib.parse.urlsplit(cfg.robot.url).hostname or cfg.robot.url
    ip = ip or local_ip_towards(host)
    payload = {"brain_host": ip, "brain_port": cfg.brain.udp_port}
    print(f"POST {cfg.robot.url}/settings {payload}")
    try:
        status, body = _robot_request(cfg, "POST", "/settings", payload)
    except OSError as e:
        print(f"robot unreachable: {e}")
        return 1
    print(f"HTTP {status}: {body[:300]}")
    return 0 if status == 200 else 1


def cmd_say(cfg: Config, text: str) -> int:
    from .robot import RobotClient
    from .speech import Speaker
    from .tts import make_tts
    from concurrent.futures import ThreadPoolExecutor

    async def go():
        robot = RobotClient(cfg.robot.url, cfg.robot.token)
        robot.start()
        sp = Speaker(make_tts(cfg.tts, cfg.resolve), robot, ThreadPoolExecutor(1))
        await sp.say(text)
        await robot.stop()

    asyncio.run(go())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chipbrain", description="Chip brain for a Tiny Engineer robot")
    ap.add_argument("--config", "-c", help="path to chip.toml (default: ./chip.toml)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the brain (default)")
    sub.add_parser("check", help="show installed pieces and robot reachability")
    pr = sub.add_parser("point-robot", help="set brain_host on the robot to this PC")
    pr.add_argument("--ip", help="IP the robot should send to (default: auto-detect)")
    sy = sub.add_parser("say", help="speak a line through the robot")
    sy.add_argument("text")
    args = ap.parse_args(argv)
    path = _find_config(args.config)
    cfg = load(path)
    _setup_logging(cfg.brain.log_level)
    if args.cmd == "check":
        return cmd_check(cfg, path)
    if args.cmd == "point-robot":
        return cmd_point_robot(cfg, args.ip)
    if args.cmd == "say":
        return cmd_say(cfg, args.text)
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        print("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
