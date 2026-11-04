import asyncio

import pytest

from chipbrain.audio import tone_wav, wav_duration_s
from chipbrain.robot import RobotClient, RobotError
from fakes import FakeRobot


def run(coro):
    return asyncio.run(coro)


def test_everything_is_serialized_and_speech_wins(robot):
    robot.play_speed = 0.3  # a 0.5 s clip blocks the fake robot for 150 ms

    async def go():
        c = RobotClient(robot.url, anim_stale_s=30)
        c.start()
        f1 = c.enqueue_play(tone_wav(0.5))
        for a in ["typing", "reading", "thinking"]:  # coalesced: only latest survives
            c.set_anim(a)
        f2 = c.enqueue_play(tone_wav(0.3))
        poses = [asyncio.create_task(c.pose([0.1, None, None, None, None], 300)) for _ in range(3)]
        await asyncio.gather(f1, f2, *poses)
        await asyncio.sleep(0.2)
        await c.stop()

    run(go())
    assert robot.overlaps == 0  # never two requests at once
    paths = robot.paths()
    assert paths[0] == "/play"
    assert robot.anims() == ["thinking"]
    assert paths.index("/anim?name=thinking") > max(i for i, p in enumerate(paths) if p == "/pose")


def test_play_sends_valid_wav_with_content_length(robot):
    async def go():
        c = RobotClient(robot.url)
        c.start()
        r = await c.play(tone_wav(0.25), pose="thinking")
        await c.stop()
        return r

    r = run(go())
    assert r["ok"] and r["played_ms"] == 250
    p = robot.plays()[0]
    assert p["path"] == "/play/thinking" and p["has_length"] and p["wav_ok"]
    assert p["headers"]["Content-Type"] == "audio/wav"
    assert int(p["headers"]["Content-Length"]) == len(p["body"])


def test_back_to_back_clips_are_merged(robot):
    robot.play_speed = 0.5

    async def go():
        c = RobotClient(robot.url)
        c.start()
        first = c.enqueue_play(tone_wav(0.4))
        await asyncio.sleep(0.05)  # first is now playing
        rest = [c.enqueue_play(tone_wav(0.2)) for _ in range(3)]
        await asyncio.gather(first, *rest)
        await c.stop()

    run(go())
    plays = robot.plays()
    assert len(plays) == 2 and all(p["wav_ok"] for p in plays)
    assert plays[1]["duration"] == pytest.approx(0.6 + 2 * 0.12, abs=0.01)


def test_urgent_animation_jumps_queue(robot):
    robot.play_speed = 0.4

    async def go():
        c = RobotClient(robot.url)
        c.start()
        f = c.enqueue_play(tone_wav(0.3))
        await asyncio.sleep(0.02)
        g = c.enqueue_play(tone_wav(0.3), pose="thinking")  # different pose: not merged
        c.set_anim("listening", urgent=True)
        await asyncio.gather(f, g)
        await c.stop()

    run(go())
    assert robot.paths() == ["/play", "/anim?name=listening", "/play/thinking"]


def test_stale_animation_dropped():
    now = [0.0]
    robot = FakeRobot().start()
    try:
        async def go():
            c = RobotClient(robot.url, anim_stale_s=5, clock=lambda: now[0])
            c.set_anim("typing")  # queued before the worker starts
            now[0] = 10.0
            c.start()
            await asyncio.sleep(0.1)
            c.set_anim("reading")
            await asyncio.sleep(0.2)
            await c.stop()

        run(go())
        assert robot.anims() == ["reading"]
    finally:
        robot.stop()


def test_busy_retry_auth_and_errors():
    robot = FakeRobot(token="s3cret").start()
    robot.busy_next = 2
    try:
        async def go():
            c = RobotClient(robot.url, token="s3cret", busy_delay_s=0.01)
            c.start()
            r = await c.get("/health")
            bad = RobotClient(robot.url, token="wrong")
            bad.start()
            with pytest.raises(RobotError) as e:
                await bad.get("/health")
            with pytest.raises(RobotError) as e2:
                await c.post("/pose", {"pose": [5, 0, 0, 0, 0], "ms": 300})
            await c.stop()
            await bad.stop()
            return r, e.value, e2.value

        r, e, e2 = run(go())
        assert r["ok"] and len([x for x in robot.requests if x.get("busy")]) == 2
        assert e.status == 401 and e2.status == 400
        assert all(x["headers"].get("Authorization") == "Bearer s3cret" for x in robot.requests[:3])
    finally:
        robot.stop()


def test_unreachable_robot_reports_and_recovers():
    async def go():
        c = RobotClient("http://127.0.0.1:9")  # nothing listens on discard
        c.start()
        with pytest.raises(RobotError):
            await c.get("/health")
        f = c.enqueue_play(tone_wav(0.1))
        with pytest.raises(RobotError):
            await f
        await c.stop()
        return c.reachable

    assert run(go()) is False


def test_tone_helper_duration():
    assert wav_duration_s(tone_wav(0.5)) == pytest.approx(0.5, abs=0.001)
