import dataclasses
import logging
from pathlib import Path

import pytest

from chipbrain.config import Config, from_dict, load

EXAMPLE = Path(__file__).resolve().parent.parent / "chip.example.toml"


def test_example_toml_documents_every_field_with_its_default(caplog):
    caplog.set_level(logging.WARNING, logger="chip.config")
    cfg = load(EXAMPLE)
    assert not caplog.records, [r.getMessage() for r in caplog.records]
    import tomllib

    data = tomllib.loads(EXAMPLE.read_text("utf-8"))
    default = Config()
    for f in dataclasses.fields(Config):
        if f.name == "base_dir":
            continue
        section = getattr(default, f.name)
        for sf in dataclasses.fields(section):
            assert sf.name in data[f.name], f"{f.name}.{sf.name} missing from chip.example.toml"
            assert getattr(getattr(cfg, f.name), sf.name) == getattr(section, sf.name), f"{f.name}.{sf.name}"


def test_overrides_types_and_unknown_keys(caplog, tmp_path):
    cfg = from_dict({"robot": {"url": "http://x", "health_poll_s": 5}, "claude": {"fast_model": "claude-haiku-5-5"},
                     "bogus": {}, "wake": {"words": ["Chip", "Robo"], "typo_key": 1}})
    assert cfg.robot.url == "http://x" and cfg.robot.health_poll_s == 5.0
    assert cfg.claude.fast_model == "claude-haiku-5-5"
    assert cfg.wake.words == ["chip", "robo"]
    assert any("typo_key" in r.getMessage() for r in caplog.records)
    with pytest.raises(ValueError):
        from_dict({"focus": {"heads_up": "yes"}})
    assert load(tmp_path / "missing.toml").robot.url == Config().robot.url
    assert cfg.resolve("a/b.json").is_absolute()


def test_local_model_section_is_read_with_claude_kept_as_backup():
    cfg = from_dict({"local": {"model": "qwen3:8b", "url": "http://pc:11434", "first_token_timeout_s": 4,
                               "enabled": False},
                     "brain": {"memory_file": "mem.json"}})
    assert cfg.local.model == "qwen3:8b"
    assert cfg.local.url == "http://pc:11434"
    assert cfg.local.first_token_timeout_s == 4.0
    assert cfg.local.enabled is False
    assert cfg.brain.memory_file == "mem.json"
    default = Config()
    assert default.local.enabled is True
    assert default.local.url == "http://127.0.0.1:11434"
    assert default.claude.model == "claude-sonnet-5-5"
