"""Idle personality: small life-like moves while nothing else is happening.

Only acts when: not in a conversation, the robot is not speaking/exploring,
the focused session has been idle >= ``min_idle_s`` (or there is none), the
robot is not asleep, and GET /anim says ``none``. So it never fights hook
animations. Poses use the firmware pose space [head, neck, left, right, body],
each -1..1 of the saved range (0 = mid), ``None`` = leave that joint.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .config import IdleCfg

Pose = list  # [head, neck, left, right, body]


@dataclass
class IdleContext:
    conversation_active: bool = False
    busy: bool = False  # speaking, exploring
    focused_idle_s: float | None = None  # None = no focused session
    asleep: bool = False
    robot_anim: str | None = None  # from GET /anim; None = unknown yet


@dataclass
class Move:
    pose: Pose
    ms: int
    hold_s: float = 0.0  # wait after starting this move before the next one
    kind: str = "glance"


@dataclass
class IdlePersonality:
    cfg: IdleCfg
    rng: random.Random = field(default_factory=random.Random)
    hour: Callable[[], int] = field(default=lambda: datetime.now().hour)
    next_glance_at: float | None = None
    stretch_at: float | None = None
    look_pending: bool = False
    last_look_at: float | None = None
    glances: int = 0

    # ---- triggers -------------------------------------------------------
    def night(self) -> bool:
        h, s, e = self.hour(), self.cfg.night_start_hour, self.cfg.night_end_hour
        return (h >= s or h < e) if s > e else (s <= h < e)

    def schedule_next(self, now: float) -> None:
        lo, hi = self.cfg.glance_min_s, self.cfg.glance_max_s
        k = 2.5 if self.night() else 1.0
        self.next_glance_at = now + self.rng.uniform(lo, hi) * k

    def on_task_finished(self, duration_s: float | None, now: float) -> None:
        if duration_s is not None and duration_s >= self.cfg.stretch_after_task_s:
            self.stretch_at = now + 8.0  # after the "ring" one-shot has played

    def on_loud_sound(self, now: float) -> bool:
        if self.last_look_at is not None and now - self.last_look_at < self.cfg.loud_glance_interval_s:
            return False
        self.look_pending = True
        return True

    # ---- decisions ------------------------------------------------------------
    def preconditions(self, ctx: IdleContext) -> bool:
        if not self.cfg.enabled or ctx.conversation_active or ctx.busy or ctx.asleep:
            return False
        return ctx.focused_idle_s is None or ctx.focused_idle_s >= self.cfg.min_idle_s

    def due(self, now: float) -> bool:
        if self.next_glance_at is None:
            self.schedule_next(now)
        return (
            self.look_pending
            or (self.stretch_at is not None and now >= self.stretch_at)
            or now >= (self.next_glance_at or now)
        )

    def take(self, now: float, ctx: IdleContext) -> list[Move]:
        """Moves to perform now (empty when conditions are not met)."""
        if not self.preconditions(ctx) or ctx.robot_anim not in ("none",):
            self.look_pending = False  # a loud-sound look goes stale at once
            if self.next_glance_at is not None and now >= self.next_glance_at:
                self.schedule_next(now)
            return []
        if self.look_pending:
            self.look_pending = False
            self.last_look_at = now
            return [self.look_at_joe()]
        if self.stretch_at is not None and now >= self.stretch_at:
            self.stretch_at = None
            self.schedule_next(now)
            return self.stretch()
        if self.next_glance_at is not None and now >= self.next_glance_at:
            self.schedule_next(now)
            if self.night() and self.rng.random() < 0.5:
                return []  # sleepy: half the glances just don't happen
            return [self.glance()]
        return []

    # ---- moves ------------------------------------------------------------------
    def look_at_joe(self) -> Move:
        return Move([self.cfg.look_at_joe_head, 0.0, None, None, 0.0], 450, 0.0, "look")

    def glance(self) -> Move:
        self.glances += 1
        a = self.cfg.amplitude * (0.5 if self.night() else 1.0)
        if self.glances % 4 == 0:
            return Move([0.0, 0.0, None, None, 0.0], 1200, kind="center")
        head = self.rng.uniform(-a, a) * 0.6 - (0.1 if self.night() else 0.0)
        neck = self.rng.uniform(-a, a)
        body = self.rng.uniform(-a, a) * 0.5 if self.rng.random() < 0.3 else None
        ms = int(self.rng.uniform(1500, 2500) if self.night() else self.rng.uniform(600, 1300))
        return Move([round(head, 3), round(neck, 3), None, None, None if body is None else round(body, 3)], ms)

    def stretch(self) -> list[Move]:
        a = min(0.45, self.cfg.amplitude * 2)
        if self.night():  # a slow yawn instead
            return [Move([a, 0.0, None, None, 0.0], 1800, 2.2, "stretch"),
                    Move([-0.1, 0.0, None, None, 0.0], 2200, 0.0, "stretch")]
        side = self.rng.choice((-1, 1))
        return [
            Move([a, 0.0, None, None, 0.0], 900, 1.4, "stretch"),
            Move([a * 0.5, side * a, None, None, side * a * 0.6], 900, 1.4, "stretch"),
            Move([a * 0.5, -side * a, None, None, -side * a * 0.6], 1100, 1.6, "stretch"),
            Move([0.0, 0.0, None, None, 0.0], 1000, 0.0, "stretch"),
        ]
