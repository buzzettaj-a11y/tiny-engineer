"""Streamed speech: text deltas -> sentences -> TTS (one worker thread) ->
robot /play queue, in order. The first sentence plays while Claude is still
writing the rest."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Executor
from typing import AsyncIterator, Callable

from .robot import RobotClient, RobotError
from .sentences import SentenceSplitter, clean_for_speech
from .tts import TTSBackend

log = logging.getLogger("chip.speech")


class Speaker:
    def __init__(self, tts: TTSBackend, robot: RobotClient, pool: Executor):
        self.tts = tts
        self.robot = robot
        self.pool = pool
        self.sentences_spoken = 0

    async def speak_stream(self, deltas: AsyncIterator[str], on_first: Callable[[], None] | None = None,
                           pose: str = "talking") -> str:
        """Speak everything ``deltas`` yields. Returns the spoken text once the
        robot has finished playing it."""
        loop = asyncio.get_running_loop()
        splitter = SentenceSplitter()
        spoken: list[str] = []
        synth_q: asyncio.Queue = asyncio.Queue()
        plays: list[asyncio.Future] = []
        first = True

        async def feeder():
            # sentences synthesise in order on one thread; enqueue in order
            while True:
                item = await synth_q.get()
                if item is None:
                    return
                text, fut = item
                try:
                    wav = await fut
                except Exception as e:
                    log.warning("TTS failed for %r: %s", text, e)
                    continue
                if wav:
                    plays.append(self.robot.enqueue_play(wav, pose))

        feeder_task = loop.create_task(feeder())

        def submit(sentence: str) -> None:
            nonlocal first
            s = clean_for_speech(sentence)
            if not s:
                return
            if first and on_first:
                on_first()
            first = False
            spoken.append(s)
            log.info("→ Chip: %s", s)
            synth_q.put_nowait((s, loop.run_in_executor(self.pool, self.tts.synthesize, s)))

        try:
            async for d in deltas:
                for s in splitter.feed(d):
                    submit(s)
            for s in splitter.flush():
                submit(s)
        finally:
            synth_q.put_nowait(None)
            await feeder_task
        for f in plays:
            try:
                await f
            except (RobotError, asyncio.CancelledError) as e:
                log.warning("could not play speech: %s", e)
        self.sentences_spoken += len(spoken)
        return " ".join(spoken)

    async def say(self, text: str, pose: str = "talking") -> str:
        async def one():
            yield text

        return await self.speak_stream(one(), pose=pose)
