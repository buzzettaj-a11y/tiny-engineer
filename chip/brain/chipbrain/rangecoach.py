"""Range coach: learns servo limits from bumps (PROTOCOL.md sections 4-5).

Rules (all angles in degrees, per joint and side):
* Repeated bumps (``bump_count`` within ``bump_window_s``) near a saved limit
  tighten that saved limit to the robot's soft limit.
* A tightened limit never crosses the saved mid and never leaves a span below
  ``min_span_deg``.
* "Find your ranges" explores every joint/side, then saves measured safe values,
  moving each limit at most ``explore_max_gain_deg`` per run.
* Loosening (voice ``adjust_range``) never goes past the last explored safe value.
* Nothing ever goes past the robot's hard caps.

Limits are sent as whole degrees, rounded toward the inside of the range.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .config import RangesCfg
from .robot import RobotClient, RobotError

log = logging.getLogger("chip.ranges")

JOINTS = ["head", "neck", "left", "right", "body"]
SPOKEN = ["head", "neck", "left arm", "right arm", "body"]
_ALIASES = {
    "head": 0, "pitch": 0, "neck": 1, "yaw": 1, "left": 2, "left hand": 2, "left arm": 2,
    "right": 3, "right hand": 3, "right arm": 3, "body": 4, "torso": 4, "waist": 4,
}


def parse_joint(j) -> int | None:
    if isinstance(j, bool):
        return None
    if isinstance(j, int):
        return j if 0 <= j < 5 else None
    s = str(j).strip().lower()
    if s.isdigit():
        return parse_joint(int(s))
    return _ALIASES.get(s)


@dataclass
class Ranges:
    saved_mins: list[float]
    saved_maxs: list[float]
    soft_mins: list[float]
    soft_maxs: list[float]
    hard_mins: list[float]
    hard_maxs: list[float]
    bumps: list[dict] = field(default_factory=list)
    sensor: bool = False

    @classmethod
    def from_json(cls, d: dict) -> "Ranges":
        def arr(k, fallback=None):
            v = d.get(k, fallback)
            if not isinstance(v, list) or len(v) != 5:
                raise ValueError(f"/ranges: bad {k}")
            return [float(x) for x in v]

        saved_mins, saved_maxs = arr("saved_mins"), arr("saved_maxs")
        return cls(
            saved_mins, saved_maxs,
            arr("soft_mins", d.get("saved_mins")), arr("soft_maxs", d.get("saved_maxs")),
            arr("hard_mins"), arr("hard_maxs"),
            list(d.get("bumps") or []), bool(d.get("sensor", False)),
        )

    def saved(self, j: int, side: str) -> float:
        return self.saved_mins[j] if side == "min" else self.saved_maxs[j]

    def soft(self, j: int, side: str) -> float:
        return self.soft_mins[j] if side == "min" else self.soft_maxs[j]

    def hard(self, j: int, side: str) -> float:
        return self.hard_mins[j] if side == "min" else self.hard_maxs[j]

    def mid(self, j: int) -> float:
        return (self.saved_mins[j] + self.saved_maxs[j]) / 2


def inward(side: str, value: float) -> int:
    """Round toward the inside of the range (conservative)."""
    return int(math.ceil(value - 1e-9)) if side == "min" else int(math.floor(value + 1e-9))


def tighter(side: str, a: float, b: float) -> bool:
    """Is ``a`` a tighter limit than ``b``?"""
    return a > b if side == "min" else a < b


def constrain(r: Ranges, j: int, side: str, value: float, min_span: float) -> int:
    """Clamp a proposed limit to the hard cap, never past the saved mid, and
    keep span >= min_span. Callers check the result still moves the way they
    intended (a range that is already too narrow is never "tightened" wider)."""
    other = r.saved_maxs[j] if side == "min" else r.saved_mins[j]
    mid = r.mid(j)
    if side == "min":
        v = min(max(value, r.hard_mins[j]), mid, other - min_span)
        out = inward("min", v)
        return out if out <= other - min_span else int(math.floor(other - min_span))
    v = max(min(value, r.hard_maxs[j]), mid, other + min_span)
    out = inward("max", v)
    return out if out >= other + min_span else int(math.ceil(other + min_span))


@dataclass
class Adjustment:
    joint: int
    side: str
    old: float
    new: float
    reason: str

    def describe(self) -> str:
        verb = "tightened" if tighter(self.side, self.new, self.old) else "loosened"
        return f"{verb} {SPOKEN[self.joint]} {self.side} {self.old:g}° → {self.new:g}° ({self.reason})"


def _bump_key(b: dict) -> tuple:
    return (b.get("t_ms"), b.get("joint"), b.get("dir"), b.get("angle"))


class BumpTracker:
    """Decides when repeated bumps should tighten a saved limit."""

    def __init__(self, cfg: RangesCfg):
        self.cfg = cfg
        self.seen: dict[tuple, dict] = {}
        self.consumed: set[tuple] = set()

    def decide(self, r: Ranges) -> list[Adjustment]:
        for b in r.bumps:
            if isinstance(b, dict) and parse_joint(b.get("joint")) is not None and b.get("dir") in ("min", "max"):
                self.seen.setdefault(_bump_key(b), b)
        groups: dict[tuple[int, str], list[dict]] = {}
        for k, b in self.seen.items():
            if k in self.consumed:
                continue
            groups.setdefault((parse_joint(b["joint"]), b["dir"]), []).append(b)
        out: list[Adjustment] = []
        window_ms = self.cfg.bump_window_s * 1000
        for (j, side), bumps in groups.items():
            limit = r.saved(j, side)
            near = [b for b in bumps if abs(float(b.get("angle", 1e9)) - limit) <= self.cfg.near_limit_deg]
            near.sort(key=lambda b: b.get("t_ms") or 0)
            hit = None
            for i in range(len(near)):
                win = [b for b in near[i:] if (b.get("t_ms") or 0) - (near[i].get("t_ms") or 0) <= window_ms]
                if len(win) >= self.cfg.bump_count:
                    hit = win
                    break
            if not hit:
                continue
            for b in bumps:  # whole group handled, do not re-trigger on old bumps
                self.consumed.add(_bump_key(b))
            soft = r.soft(j, side)
            if tighter(side, soft, limit):
                target = soft
            else:  # soft limits were reset; derive from the bump angles (2 deg inside)
                angles = [float(b["angle"]) for b in hit]
                target = (max(angles) + 2) if side == "min" else (min(angles) - 2)
            new = constrain(r, j, side, target, self.cfg.min_span_deg)
            if tighter(side, new, limit):
                out.append(Adjustment(j, side, limit, new, f"{len(hit)} bumps"))
        return out


def apply(r: Ranges, adjs: list[Adjustment]) -> dict:
    mins, maxs = list(r.saved_mins), list(r.saved_maxs)
    for a in adjs:
        (mins if a.side == "min" else maxs)[a.joint] = a.new
    clean = lambda xs: [int(x) if float(x).is_integer() else x for x in xs]  # noqa: E731
    return {"servo_mins": clean(mins), "servo_maxs": clean(maxs)}


def explore_stop_message(e: "RobotError") -> str | None:
    """Spoken reason to abandon a whole explore run, or None to just skip one side.

    The firmware refuses explore with 409 when the current sensor is missing,
    not answering, or reads implausibly (e.g. VIN+/VIN- swapped), and with 500
    when the sensor fails mid-sweep. None of those get better on the next joint.
    """
    err = ""
    if isinstance(e.body, dict):
        err = str(e.body.get("error", ""))
    err = (err or str(e)).lower()
    if e.status == 409:
        if "no current sensor" in err:
            return "I can't feel my limits without the current sensor."
        if "not responding" in err:
            return "My current sensor isn't answering, so I stopped. Check its wires."
        if "implausible" in err:
            return ("My current sensor reading looks wrong, so I stopped. "
                    "Check that its VIN plus and VIN minus wires aren't swapped.")
        return "I couldn't check my limits: " + err + "."
    if e.status == 500 and "steady" in err:
        return "I couldn't get a steady reading from my current sensor, so I stopped."
    if e.status == 500 and "sensor" in err:
        return "My current sensor stopped answering halfway, so I stopped."
    return None


def plan_explore(r: Ranges, results: list[dict], max_gain: float, min_span: float) -> list[Adjustment]:
    """Turn /ranges/explore results into new saved limits (grow <= max_gain)."""
    out: list[Adjustment] = []
    for res in results:
        j, side = parse_joint(res.get("joint")), res.get("dir")
        if j is None or side not in ("min", "max") or res.get("safe") is None:
            continue
        old = r.saved(j, side)
        safe = float(res["safe"])
        if tighter(side, safe, old):
            target = safe  # bumped before the old limit: tighten to what is safe
        else:
            target = max(safe, old - max_gain) if side == "min" else min(safe, old + max_gain)
        new = constrain(r, j, side, target, min_span)
        wanted_tighter = tighter(side, safe, old)
        if abs(new - old) >= 1 and tighter(side, new, old) == wanted_tighter:
            out.append(Adjustment(j, side, old, new, f"explored, safe {safe:g}°"))
    return out


class State:
    """Small JSON file: explored safe values survive restarts."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict = {"explored": {}}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text("utf-8"))
                self.data.setdefault("explored", {})
            except (OSError, ValueError) as e:
                log.warning("could not read %s: %s", path, e)

    def explored(self, j: int, side: str) -> float | None:
        v = self.data["explored"].get(str(j), {}).get(side)
        return float(v) if v is not None else None

    def set_explored(self, j: int, side: str, value: float) -> None:
        self.data["explored"].setdefault(str(j), {})[side] = value

    def save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("could not save %s: %s", self.path, e)


Say = Callable[[str], Awaitable[None]]


class RangeCoach:
    def __init__(self, robot: RobotClient, cfg: RangesCfg, state: State, say: Say | None = None,
                 mention: Callable[[str], None] | None = None):
        self.robot = robot
        self.cfg = cfg
        self.state = state
        self.tracker = BumpTracker(cfg)
        self.say = say
        self.mention = mention
        self.exploring = False
        self.sensor: bool | None = None
        self.log: list[str] = []

    async def fetch(self) -> Ranges:
        return Ranges.from_json(await self.robot.get("/ranges"))

    async def poll_once(self) -> list[Adjustment]:
        if self.exploring:
            return []
        try:
            r = await self.fetch()
        except (RobotError, ValueError) as e:
            log.debug("ranges poll failed: %s", e)
            return []
        self.sensor = r.sensor
        adjs = self.tracker.decide(r)
        if not adjs:
            return []
        try:
            await self.robot.post("/ranges", apply(r, adjs))
        except RobotError as e:
            log.warning("range coach: robot refused new limits: %s", e)
            return []
        for a in adjs:
            msg = a.describe()
            log.info("range coach: %s", msg)
            self.log.append(msg)
        if self.mention:
            parts = sorted({SPOKEN[a.joint] for a in adjs})
            self.mention(f"I kept bumping my {' and '.join(parts)}, so I tightened that range a little.")
        return adjs

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.poll_s)
            try:
                await self.poll_once()
            except Exception:  # never kill the loop
                log.exception("range coach poll crashed")

    async def explore(self, joints: list[int] | None = None) -> str:
        if self.exploring:
            return "Already exploring."
        self.exploring = True
        try:
            r = await self.fetch()
            joints = joints if joints is not None else list(range(5))
            results: list[dict] = []
            for j in joints:
                if self.say:
                    await self.say(f"Checking my {SPOKEN[j]}.")
                for side in ("min", "max"):
                    try:
                        res = await self.robot.post(
                            "/ranges/explore", {"joint": j, "dir": side, "speed": self.cfg.explore_speed}, timeout=120)
                    except RobotError as e:
                        stop = explore_stop_message(e)
                        if stop:
                            log.warning("explore %s %s stopped: %s", JOINTS[j], side, e)
                            return stop
                        log.warning("explore %s %s failed: %s", JOINTS[j], side, e)
                        continue
                    res.setdefault("joint", j)
                    res.setdefault("dir", side)
                    log.info("explore %s %s: %s", JOINTS[j], side, res)
                    results.append(res)
                    if res.get("safe") is not None:
                        # loosening by voice is capped at the LAST explored value
                        self.state.set_explored(j, side, float(res["safe"]))
            self.state.save()
            adjs = plan_explore(r, results, self.cfg.explore_max_gain_deg, self.cfg.min_span_deg)
            if not adjs:
                return "My ranges look right already."
            await self.robot.post("/ranges", apply(r, adjs))
            for a in adjs:
                self.log.append(a.describe())
                log.info("explore: %s", a.describe())
            grown = [a for a in adjs if not tighter(a.side, a.new, a.old)]
            shrunk = [a for a in adjs if tighter(a.side, a.new, a.old)]
            bits = []
            if grown:
                bits.append("more room on my " + " and ".join(sorted({SPOKEN[a.joint] for a in grown})))
            if shrunk:
                bits.append("less on my " + " and ".join(sorted({SPOKEN[a.joint] for a in shrunk})))
            return "Done. I saved " + ", and ".join(bits) + "."
        except RobotError as e:
            return f"Exploring failed: {e}"
        finally:
            self.exploring = False

    async def adjust(self, joint, side: str, action: str, degrees: float) -> str:
        j = parse_joint(joint)
        if j is None:
            return f"Unknown joint {joint!r}; use head, neck, left, right or body."
        if side not in ("min", "max"):
            return "dir must be min or max."
        if action not in ("tighten", "loosen"):
            return "action must be tighten or loosen."
        degrees = max(1.0, min(float(degrees), self.cfg.adjust_max_deg))
        r = await self.fetch()
        old = r.saved(j, side)
        inward_sign = 1 if side == "min" else -1
        if action == "tighten":
            target = old + inward_sign * degrees
        else:
            explored = self.state.explored(j, side)
            if explored is None:
                return (f"I haven't explored my {SPOKEN[j]} {side} yet, so I won't loosen it. "
                        "Ask me to find my ranges first.")
            target = old - inward_sign * degrees
            if tighter(side, explored, target):
                target = explored
            if not tighter(side, old, target):
                return f"My {SPOKEN[j]} {side} is already at the explored safe limit."
        new = constrain(r, j, side, target, self.cfg.min_span_deg)
        moved_tighter = tighter(side, new, old)
        if abs(new - old) < 1 or moved_tighter != (action == "tighten"):
            return f"Can't {action} my {SPOKEN[j]} {side} any more: span, mid or hard limit."
        adj = Adjustment(j, side, old, new, f"voice {action}")
        await self.robot.post("/ranges", apply(r, [adj]))
        self.log.append(adj.describe())
        log.info("adjust: %s", adj.describe())
        return f"Done, {adj.describe()}."
