"""Configuration: dataclasses with documented defaults, loaded from chip.toml.

Every field can be overridden in the matching ``[section]`` of chip.toml.
Unknown keys are reported (typos are the #1 config bug) but do not stop the
brain. ANTHROPIC_API_KEY always comes from the environment.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("chip.config")


@dataclass
class RobotCfg:
    url: str = "http://192.168.86.223"  # robot base URL
    token: str = ""  # robot access_token (Bearer), empty = no auth
    health_poll_s: float = 30.0
    anim_stale_s: float = 8.0  # queued animation older than this is dropped


@dataclass
class BrainCfg:
    http_host: str = "0.0.0.0"
    http_port: int = 47101  # hook intake + /status /say /focus
    udp_port: int = 47100  # mic stream from the robot
    token: str = ""  # optional shared secret for brain HTTP (X-Chip-Token or Bearer)
    state_file: str = "chip_state.json"  # explored ranges etc.
    log_level: str = "INFO"


@dataclass
class AudioCfg:
    vad: str = "auto"  # auto | energy | webrtc
    webrtc_aggressiveness: int = 2  # 0..3
    end_silence_ms: int = 600  # silence that ends an utterance
    min_speech_ms: int = 300  # shorter blips are noise, not speech
    max_segment_s: float = 15.0
    preroll_ms: int = 300  # audio kept before speech start
    prefix_s: float = 1.2  # early wake check after this much speech
    energy_margin_db: float = 10.0  # speech = this much above noise floor
    min_speech_dbfs: float = -50.0  # never speech below this level
    loud_sound_dbfs: float = -14.0  # short non-speech blips louder than this = "loud sound"
    max_gap_fill_packets: int = 40  # lost packets filled with silence up to this


@dataclass
class WakeCfg:
    # Accepted anywhere in the first `max_position` words.
    words: list[str] = field(default_factory=lambda: ["chip", "chipp", "chips", "chipper", "chippy"])
    # Risky sound-alikes: only accepted right after a greeting ("hey ship").
    words_after_greeting: list[str] = field(
        default_factory=lambda: ["ship", "chap", "chop", "cheap", "jip", "chet", "tip", "trip"]
    )
    greetings: list[str] = field(default_factory=lambda: ["hey", "hi", "okay", "ok", "hay", "yo", "oh", "hello"])
    max_position: int = 2  # wake word must be one of the first N words (after greetings)
    follow_up_s: float = 8.0  # after Chip speaks, talk without the wake word for this long
    ack: str = "Yes?"  # said when Joe only says "Chip". Empty = just look at him
    openwakeword_model: str = ""  # path to an openWakeWord .onnx/.tflite model (optional)
    openwakeword_threshold: float = 0.5


@dataclass
class SttCfg:
    model: str = "base.en"  # faster-whisper model name or path
    device: str = "cpu"
    compute_type: str = "int8"
    # Biases Whisper toward spelling the name "Chip". Deliberately NOT "Hey Chip":
    # Whisper sometimes echoes its prompt on unclear audio, and an echo must
    # never look like a wake phrase.
    initial_prompt: str = "Talking to Chip, a desk robot."
    beam_size: int = 1


@dataclass
class ClaudeCfg:
    model: str = "claude-sonnet-5-5"
    fast_model: str = ""  # e.g. "claude-haiku-5-5" for short casual chat; empty = off
    max_tokens: int = 300
    history_turns: int = 6  # Joe/Chip exchanges kept
    max_tool_rounds: int = 4
    owner_name: str = "Joe"


@dataclass
class TtsCfg:
    backend: str = "auto"  # auto | piper | sapi | none
    piper_model: str = ""  # path to a .onnx voice (its .onnx.json must sit next to it)
    piper_exe: str = ""  # optional piper.exe if the python package is not installed
    sapi_voice: str = ""  # substring of a Windows voice name, e.g. "Zira"
    sapi_rate: int = 1  # -10..10
    volume: float = 1.0  # 0..2 gain applied to the WAV


@dataclass
class FocusCfg:
    auto_switch_idle_s: float = 120.0  # auto mode leaves a session only after this idle
    heads_up: bool = True
    heads_up_session_interval_s: float = 300.0  # per session
    heads_up_global_interval_s: float = 30.0  # across sessions
    forget_ended_after_s: float = 1800.0
    forget_idle_after_s: float = 12 * 3600.0


@dataclass
class RangesCfg:
    enabled: bool = True
    poll_s: float = 30.0
    bump_count: int = 2
    bump_window_s: float = 600.0
    near_limit_deg: float = 8.0  # bump this close to the saved limit counts
    min_span_deg: float = 12.0
    explore_max_gain_deg: float = 15.0  # per limit, per run
    explore_speed: int = 10  # deg/s, robot accepts 5..30
    adjust_max_deg: float = 20.0  # biggest single voice adjustment


@dataclass
class IdleCfg:
    enabled: bool = True
    min_idle_s: float = 60.0  # focused session idle this long before fidgets
    glance_min_s: float = 20.0
    glance_max_s: float = 90.0
    amplitude: float = 0.2  # pose units (-1..1 of saved range)
    stretch_after_task_s: float = 300.0  # tasks at least this long earn a stretch
    loud_glance_interval_s: float = 30.0
    night_start_hour: int = 23
    night_end_hour: int = 7
    look_at_joe_head: float = 0.25  # head pose that faces Joe (+ = up, firmware pose space)


@dataclass
class Config:
    robot: RobotCfg = field(default_factory=RobotCfg)
    brain: BrainCfg = field(default_factory=BrainCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    wake: WakeCfg = field(default_factory=WakeCfg)
    stt: SttCfg = field(default_factory=SttCfg)
    claude: ClaudeCfg = field(default_factory=ClaudeCfg)
    tts: TtsCfg = field(default_factory=TtsCfg)
    focus: FocusCfg = field(default_factory=FocusCfg)
    ranges: RangesCfg = field(default_factory=RangesCfg)
    idle: IdleCfg = field(default_factory=IdleCfg)
    base_dir: Path = field(default_factory=Path.cwd)

    def resolve(self, p: str) -> Path:
        path = Path(os.path.expandvars(os.path.expanduser(p)))
        return path if path.is_absolute() else self.base_dir / path


def _coerce(value: Any, default: Any, where: str) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{where} must be true or false")
        return value
    if isinstance(default, float) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(default, int) and isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(default, str) and isinstance(value, str):
        return value
    if isinstance(default, list) and isinstance(value, list):
        return [str(v).lower() for v in value]
    raise ValueError(f"{where} has the wrong type (expected {type(default).__name__})")


def from_dict(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    cfg = Config()
    if base_dir is not None:
        cfg.base_dir = base_dir
    for section, values in data.items():
        target = getattr(cfg, section, None)
        if target is None or not dataclasses.is_dataclass(target) or not isinstance(values, dict):
            log.warning("config: unknown section [%s] ignored", section)
            continue
        names = {f.name for f in dataclasses.fields(target)}
        for key, value in values.items():
            if key not in names:
                log.warning("config: unknown key %s.%s ignored", section, key)
                continue
            setattr(target, key, _coerce(value, getattr(target, key), f"{section}.{key}"))
    return cfg


def load(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    p = Path(path)
    if not p.exists():
        log.warning("config: %s not found, using defaults", p)
        return Config(base_dir=p.parent.resolve())
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return from_dict(data, base_dir=p.parent.resolve())
