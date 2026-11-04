import asyncio

import pytest

from chipbrain.config import RangesCfg
from chipbrain.rangecoach import BumpTracker, RangeCoach, Ranges, State, constrain, plan_explore
from chipbrain.robot import RobotClient


def ranges(**over):
    d = dict(saved_mins=[20, 30, 10, 100, 40], saved_maxs=[140, 150, 80, 170, 140],
             soft_mins=[20, 30, 10, 100, 40], soft_maxs=[140, 150, 80, 170, 140],
             hard_mins=[0] * 5, hard_maxs=[180] * 5, sensor=True, bumps=[])
    d.update(over)
    return Ranges.from_json(d)


def bump(t_s, joint=0, d="max", angle=139.0):
    return {"t_ms": int(t_s * 1000), "joint": joint, "angle": angle, "dir": d, "current_ma": 900, "anim": "typing"}


def test_two_bumps_near_limit_tighten_to_soft():
    r = ranges(soft_maxs=[136, 150, 80, 170, 140], bumps=[bump(100), bump(400, angle=138)])
    adjs = BumpTracker(RangesCfg()).decide(r)
    assert len(adjs) == 1
    a = adjs[0]
    assert (a.joint, a.side, a.old, a.new) == (0, "max", 140, 136)


def test_bumps_too_far_apart_or_far_from_limit_do_nothing():
    t = BumpTracker(RangesCfg())
    assert t.decide(ranges(soft_maxs=[136, 150, 80, 170, 140], bumps=[bump(0), bump(700)])) == []
    t2 = BumpTracker(RangesCfg())
    assert t2.decide(ranges(soft_maxs=[100, 150, 80, 170, 140],
                            bumps=[bump(0, angle=100), bump(10, angle=101)])) == []  # mid-range: obstacle, not limit
    t3 = BumpTracker(RangesCfg())
    assert t3.decide(ranges(bumps=[bump(0)])) == []  # one bump only


def test_bumps_not_reused_and_soft_reset_fallback():
    t = BumpTracker(RangesCfg())
    r = ranges(bumps=[bump(0, joint=1, d="min", angle=32), bump(30, joint=1, d="min", angle=33)])
    adjs = t.decide(r)  # soft == saved (reset) -> derive: max angle + 2
    assert [(a.joint, a.side, a.new) for a in adjs] == [(1, "min", 35)]
    assert t.decide(r) == []  # same bumps again: nothing new
    r.bumps.append(bump(60, joint=1, d="min", angle=34))
    assert t.decide(r) == []  # one fresh bump is not enough


def test_constrain_never_crosses_mid_or_min_span():
    r = ranges(saved_mins=[20, 30, 10, 100, 40], saved_maxs=[40, 150, 80, 170, 140])
    assert constrain(r, 0, "max", 25, 12) == 32  # min + 12
    assert constrain(r, 0, "min", 39, 12) == 28  # max - 12
    r2 = ranges()
    assert constrain(r2, 0, "max", 50, 12) == 80  # mid of 20..140
    assert constrain(r2, 0, "max", 999, 12) == 180  # hard cap
    assert constrain(r2, 0, "min", -50, 12) == 0


def test_tracker_never_tightens_an_already_narrow_range_wider():
    r = ranges(saved_mins=[20, 30, 10, 100, 40], saved_maxs=[30, 150, 80, 170, 140],
               soft_maxs=[28, 150, 80, 170, 140], bumps=[bump(0, angle=29), bump(5, angle=29)])
    assert BumpTracker(RangesCfg()).decide(r) == []


def test_plan_explore_caps_growth_and_tightens():
    r = ranges()
    res = [
        {"joint": 0, "dir": "max", "safe": 175},  # +35 found, grow only 15
        {"joint": 0, "dir": "min", "safe": 25},  # bumped inside the saved range -> tighten
        {"joint": 1, "dir": "min", "safe": 26},  # +4 -> take it
        {"joint": 2, "dir": "max", "safe": 80.4},  # no real change
    ]
    adjs = {(a.joint, a.side): a.new for a in plan_explore(r, res, 15, 12)}
    assert adjs == {(0, "max"): 155, (0, "min"): 25, (1, "min"): 26}


def _run(coro):
    return asyncio.run(coro)


def test_coach_poll_posts_new_limits(robot):
    robot.soft_maxs[0] = 136
    robot.bumps = [bump(400, angle=138), bump(100)]

    async def go():
        client = RobotClient(robot.url)
        client.start()
        said = []
        coach = RangeCoach(client, RangesCfg(), State(None), mention=said.append)
        adjs = await coach.poll_once()
        again = await coach.poll_once()
        await client.stop()
        return adjs, again, said

    adjs, again, said = _run(go())
    assert len(adjs) == 1 and again == []
    assert robot.saved_maxs[0] == 136 and robot.saved_mins == [20, 30, 10, 100, 40]
    assert said == ["I kept bumping my head, so I tightened that range a little."]


def test_explore_then_adjust_loosen_capped_by_explored(robot, tmp_path):
    robot.explore_safe = {(0, "max"): 170, (0, "min"): 12}

    async def go():
        client = RobotClient(robot.url)
        client.start()
        narr = []

        async def say(t):
            narr.append(t)

        state = State(tmp_path / "state.json")
        coach = RangeCoach(client, RangesCfg(), state, say=say)
        summary = await coach.explore([0])
        after_explore = (list(robot.saved_mins), list(robot.saved_maxs))
        # loosen max by 20: explored safe is 170, current 155 -> stops at 170
        msg = await coach.adjust("head", "max", "loosen", 20)
        loosened = robot.saved_maxs[0]
        msg2 = await coach.adjust("head", "max", "loosen", 5)
        # neck never explored -> refuse loosening
        msg3 = await coach.adjust("neck", "min", "loosen", 5)
        msg4 = await coach.adjust("neck", "min", "tighten", 5)
        await client.stop()
        return summary, narr, after_explore, msg, loosened, msg2, msg3, msg4, state

    summary, narr, after, msg, loosened, msg2, msg3, msg4, state = _run(go())
    assert narr == ["Checking my head."]
    assert after[0][0] == 12 and after[1][0] == 155  # min +8 (<15), max +15 cap
    assert "more room on my head" in summary
    assert loosened == 170 and "loosened" in msg
    assert "already at the explored safe limit" in msg2
    assert "won't loosen" in msg3 and robot.saved_mins[1] == 35 and "tightened" in msg4
    assert State(state.path).explored(0, "max") == 170  # persisted


def test_explore_without_sensor(robot):
    robot.sensor = False

    async def go():
        client = RobotClient(robot.url)
        client.start()
        coach = RangeCoach(client, RangesCfg(), State(None))
        out = await coach.explore([0])
        await client.stop()
        return out

    assert "current sensor" in _run(go())


@pytest.mark.parametrize("joint", ["elbow", 7, True])
def test_adjust_rejects_bad_joint(robot, joint):
    async def go():
        client = RobotClient(robot.url)
        client.start()
        out = await RangeCoach(client, RangesCfg(), State(None)).adjust(joint, "max", "tighten", 5)
        await client.stop()
        return out

    assert "Unknown joint" in _run(go())


import pytest


@pytest.mark.parametrize("status,error,expect", [
    (409, "current sensor not responding", "isn't answering"),
    (409, "implausible servo current; check INA219 VIN+/VIN- wiring", "swapped"),
    (500, "current sensor failed during explore", "stopped answering halfway"),
    (500, "could not read a steady servo current", "steady reading"),
])
def test_explore_sensor_faults_stop_the_run(robot, status, error, expect):
    robot.explore_error = (status, error)

    async def go():
        client = RobotClient(robot.url)
        client.start()
        coach = RangeCoach(client, RangesCfg(), State(None))
        out = await coach.explore([0, 1])
        await client.stop()
        return out

    before = (list(robot.saved_mins), list(robot.saved_maxs))
    assert expect in _run(go())
    assert (robot.saved_mins, robot.saved_maxs) == before  # nothing saved
