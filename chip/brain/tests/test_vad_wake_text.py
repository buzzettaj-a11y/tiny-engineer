import numpy as np
import pytest

from chipbrain.audio import StreamResampler
from chipbrain.config import AudioCfg, WakeCfg
from chipbrain.conversation import Conversation
from chipbrain.sentences import SentenceSplitter, clean_for_speech
from chipbrain.vad import Noise, Prefix, Segment, Segmenter, SpeechStart
from chipbrain.wake import match_wake
from fakes import silence, speech_like


def run_segmenter(chunks, cfg=None):
    cfg = cfg or AudioCfg()
    seg = Segmenter(cfg)
    rs = StreamResampler(22050, 16000)
    events = []
    x = np.concatenate(chunks)
    for i in range(0, len(x), 256):  # packet-sized feeding, like the robot
        events += seg.feed(rs.process(x[i:i + 256]))
    return events


def kinds(events):
    return [type(e).__name__ for e in events]


def test_single_utterance_with_short_pause_is_one_segment():
    ev = run_segmenter([silence(1.0), speech_like(0.8), silence(0.35), speech_like(0.9, seed=3), silence(1.0)])
    segs = [e for e in ev if isinstance(e, Segment)]
    assert len(segs) == 1
    s = segs[0]
    assert s.t_start == pytest.approx(1.0, abs=0.35)  # includes pre-roll
    assert s.t_end == pytest.approx(1.0 + 0.8 + 0.35 + 0.9 + 0.6, abs=0.15)  # ends ~600 ms after speech
    assert 1.4 < len(s.audio) / 16000 < 2.6
    assert isinstance(ev[0], SpeechStart)


def test_two_utterances_split_by_long_silence():
    ev = run_segmenter([silence(0.5), speech_like(0.7), silence(1.2), speech_like(0.7, seed=5), silence(0.8)])
    assert kinds(ev).count("Segment") == 2


def test_end_silence_is_tunable():
    cfg = AudioCfg(end_silence_ms=300)
    ev = run_segmenter([silence(0.5), speech_like(0.7), silence(0.45), speech_like(0.7, seed=5), silence(0.8)], cfg)
    assert kinds(ev).count("Segment") == 2


def test_prefix_only_for_long_utterances():
    long = run_segmenter([silence(0.5), speech_like(2.0), silence(1.0)])
    pre = [e for e in long if isinstance(e, Prefix)]
    assert len(pre) == 1
    assert len(pre[0].audio) / 16000 == pytest.approx(1.2, abs=0.05)
    assert kinds(long).index("Prefix") < kinds(long).index("Segment")
    short = run_segmenter([silence(0.5), speech_like(0.8), silence(1.0)])
    assert "Prefix" not in kinds(short) and "Segment" in kinds(short)


def test_click_is_loud_noise_not_speech():
    click = np.zeros(int(0.12 * 22050), np.float32)
    click[:] = 0.7 * np.sign(np.sin(np.arange(len(click))))
    ev = run_segmenter([silence(1.0), click, silence(1.0)])
    assert "Segment" not in kinds(ev)
    noise = [e for e in ev if isinstance(e, Noise)]
    assert noise and noise[0].loud


def test_max_segment_cuts_long_speech():
    ev = run_segmenter([silence(0.3), speech_like(5.0), silence(0.8)], AudioCfg(max_segment_s=2.0))
    assert kinds(ev).count("Segment") >= 2


def test_reset_drops_partial():
    seg = Segmenter(AudioCfg())
    rs = StreamResampler(22050, 16000)
    seg.feed(rs.process(silence(0.5)))
    seg.feed(rs.process(speech_like(0.5)))
    assert seg.in_speech
    seg.reset()
    assert not seg.in_speech


@pytest.mark.parametrize("text,rest", [
    ("Hey Chip, what's JobSearch doing?", "what's JobSearch doing?"),
    ("Chip. Switch to Flipopolis.", "Switch to Flipopolis."),
    ("okay chip mute yourself", "mute yourself"),
    ("Hey, Chipp! status", "status"),
    ("Hey chips how are we doing", "how are we doing"),
    ("Oh hey Chip", ""),
    ("hey ship, what's up", "what's up"),
    ("Um, Chip, are the tests passing?", "are the tests passing?"),
    ("Hey-Chip go to auto", "go to auto"),
])
def test_wake_matches(text, rest):
    m = match_wake(text, WakeCfg())
    assert m.matched, text
    assert m.rest == rest


@pytest.mark.parametrize("text", [
    "ship it",
    "We should ship this today",
    "cheap flights to Denver",
    "I ate some chips",  # name not at the start
    "the chip shortage is bad",
    "hey there",
    "",
    "Hey can you pass the chips",
])
def test_wake_rejects(text):
    assert not match_wake(text, WakeCfg()).matched, text


def test_wake_list_configurable():
    cfg = WakeCfg(words=["robo"], words_after_greeting=[])
    assert match_wake("robo, hello", cfg).matched
    assert not match_wake("chip, hello", cfg).matched


def test_conversation_window():
    c = Conversation(follow_up_s=8, history_turns=2)
    assert not c.accepts_without_wake(0)
    c.start_reply()
    assert c.active(1)
    c.reply_done(10)
    assert c.accepts_without_wake(17.9) and not c.accepts_without_wake(18.1)
    assert not c.expire(17)
    assert c.expire(18.5) and not c.expire(19)
    assert not c.active(19)
    c.woke(30)
    assert c.accepts_without_wake(37) and c.expire(38.5)
    assert not c.expire(39) and not c.accepts_without_wake(39)


def test_conversation_messages_alternate_and_trim():
    c = Conversation(history_turns=2)
    for i in range(4):
        c.add("user", f"q{i}")
        c.add("assistant", f"a{i}")
    msgs = c.messages()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "q2"


def test_sentence_splitter_streaming():
    sp = SentenceSplitter()
    out = []
    for d in ["Sure", ". The tests in v1.2 pa", "ssed 3.5 times, e.g. today", "! Dr. Who is ", "fine? Ok"]:
        out += sp.feed(d)
    out += sp.flush()
    assert out == ["Sure.", "The tests in v1.2 passed 3.5 times, e.g. today!", "Dr. Who is fine?", "Ok"]


def test_sentence_splitter_first_sentence_asap():
    sp = SentenceSplitter()
    assert sp.feed("JobSearch is running tests. ") == ["JobSearch is running tests."]
    assert sp.feed("Two failed") == []


def test_sentence_splitter_long_runon_cut_at_comma():
    sp = SentenceSplitter(max_chars=80)
    text = "word " * 12 + ", " + "more " * 20
    out = sp.feed(text)
    assert out and len(out[0]) <= 80


def test_clean_for_speech():
    assert clean_for_speech("**Done** - see `app.py` at https://x.y/z") == "Done - see app.py at a link"
    assert clean_for_speech("- one\n- two") == "one two"


def test_whisper_prompt_echo_never_wakes():
    from chipbrain.config import SttCfg

    assert not match_wake(SttCfg().initial_prompt, WakeCfg()).matched


def test_talking_while_chip_thinks_needs_no_wake_word():
    c = Conversation()
    c.start_reply()
    assert c.accepts_without_wake(0)
