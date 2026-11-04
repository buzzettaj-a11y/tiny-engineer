from chipbrain.animmap import animation_for_event
from chipbrain.config import FocusCfg
from chipbrain.focus import FocusManager, HeadsUp, spoken
from chipbrain.sessions import SessionStore


def ev(name, host="VM-2", sid="s1", cwd=r"C:\Users\joe\code\JobSearch", **kw):
    return {"hook_event_name": name, "host": host, "session_id": sid, "cwd": cwd, **kw}


def test_animation_map_matches_map_js():
    assert animation_for_event(ev("PreToolUse", tool_name="Read")) == "reading"
    assert animation_for_event(ev("PreToolUse", tool_name="Bash")) == "typing"
    assert animation_for_event(ev("PreToolUse", tool_name="Task")) is None
    assert animation_for_event(ev("SessionStart", source="startup")) == "welcome"
    assert animation_for_event(ev("SessionStart", source="resume")) == "wakeup"
    assert animation_for_event(ev("SessionEnd", reason="clear")) is None
    assert animation_for_event(ev("SessionEnd", reason="logout")) == "sleep"
    for name, anim in [("UserPromptSubmit", "reading"), ("SubagentStart", "thinking"), ("PostToolBatch", "thinking"),
                       ("PostToolUseFailure", "error"), ("PreCompact", "thinking"), ("PermissionRequest", "attention"),
                       ("PermissionDenied", "abort"), ("Notification", "attention"), ("Stop", "ring"),
                       ("StopFailure", "error")]:
        assert animation_for_event(ev(name)) == anim
    assert animation_for_event({}) is None


def test_session_labels_and_details():
    st = SessionStore()
    s = st.ingest(ev("SessionStart", source="startup"), 0)
    assert s.label == "JobSearch on VM-2"
    st.ingest(ev("UserPromptSubmit", prompt="fix the failing login test"), 1)
    st.ingest(ev("PreToolUse", tool_name="Edit", tool_input={"file_path": r"C:\code\JobSearch\app\login.py"}), 2)
    st.ingest(ev("PreToolUse", tool_name="Bash", tool_input={"command": "pytest -q tests/test_login.py"}), 3)
    st.ingest(ev("PostToolUseFailure", tool_name="Bash", tool_input={"command": "pytest"}, error="1 failed"), 4)
    st.ingest(ev("Stop"), 64)
    d = s.describe(70, detail=True)
    assert "fix the failing login test" in d and "login.py" in d and "Bash pytest" in d and "1 failed" in d
    assert s.waiting and s.last_task_s == 63
    # same folder on same host, different session -> numbered
    s2 = st.ingest(ev("SessionStart", sid="s2"), 80)
    assert s2.label == "JobSearch #2 on VM-2"
    # posix cwd, other host
    s3 = st.ingest(ev("SessionStart", host="VM-1", sid="x", cwd="/home/joe/Flipopolis"), 81)
    assert s3.label == "Flipopolis on VM-1"


def test_find_by_speech():
    st = SessionStore()
    st.ingest(ev("SessionStart", host="VM-2", cwd="C:/code/JobSearch"), 0)
    st.ingest(ev("SessionStart", host="VM-1", sid="b", cwd="C:/code/Flipopolis"), 1)
    assert st.find("job search").folder == "JobSearch"
    assert st.find("flip opolis on VM one").folder == "Flipopolis"
    assert st.find("Flipopolis on VM-1").host == "VM-1"
    assert st.find("VM-2/s1").folder == "JobSearch"
    assert st.find("banana bread") is None


def make(idle_s=120):
    st = SessionStore()
    return st, FocusManager(st, FocusCfg(auto_switch_idle_s=idle_s))


def test_auto_focus_first_session_and_no_switch_while_busy():
    st, fm = make()
    a = st.ingest(ev("UserPromptSubmit", host="VM-2", cwd="C:/c/JobSearch"), 0)
    ch = fm.on_event(a, {}, 0)
    assert ch and ch.session is a and ch.announce == "Watching JobSearch on VM 2."
    b = st.ingest(ev("UserPromptSubmit", host="VM-1", sid="b", cwd="C:/c/Flipopolis"), 60)
    assert fm.on_event(b, {}, 60) is None  # A idle only 60 s
    assert fm.focused is a


def test_auto_focus_switches_after_idle_and_announces():
    st, fm = make()
    a = st.ingest(ev("UserPromptSubmit"), 0)
    fm.on_event(a, {}, 0)
    b = st.ingest(ev("PreToolUse", host="VM-1", sid="b", cwd="/x/Flipopolis", tool_name="Bash"), 100)
    assert fm.on_event(b, {}, 100) is None
    ch = fm.evaluate(121)  # A now idle 121 s; B more recent
    assert ch and ch.session is b and ch.previous is a
    assert ch.announce == "Switching to Flipopolis on VM 1."
    assert fm.evaluate(122) is None  # stable


def test_notification_is_not_activity():
    st, fm = make()
    a = st.ingest(ev("UserPromptSubmit"), 0)
    fm.on_event(a, {}, 0)
    st.ingest(ev("Notification", message="Claude is waiting"), 100)
    assert a.idle_s(130) == 130


def test_auto_focus_switches_when_current_ends():
    st, fm = make()
    a = st.ingest(ev("UserPromptSubmit"), 0)
    fm.on_event(a, {}, 0)
    b = st.ingest(ev("UserPromptSubmit", host="VM-1", sid="b", cwd="/x/Flipopolis"), 5)
    fm.on_event(b, {}, 5)
    assert fm.focused is a
    e = ev("SessionEnd", reason="logout")
    st.ingest(e, 10)
    ch = fm.on_event(a, e, 10)
    assert ch and ch.session is b


def test_explicit_focus_sticks_then_auto():
    st, fm = make()
    a = st.ingest(ev("UserPromptSubmit"), 0)
    fm.on_event(a, {}, 0)
    b = st.ingest(ev("UserPromptSubmit", host="VM-1", sid="b", cwd="/x/Flipopolis"), 1)
    ok, msg, ch = fm.set_focus("flipopolis", 2)
    assert ok and fm.focused is b and fm.mode == "explicit"
    st.ingest(ev("PreToolUse", tool_name="Bash"), 500)
    assert fm.on_event(a, {}, 500) is None and fm.focused is b  # explicit wins
    ok, msg, _ = fm.set_focus("nope", 501)
    assert not ok and "Known" in msg
    ok, msg, ch = fm.set_focus(None, 502)
    assert ok and fm.mode == "auto"
    assert fm.focused is a  # b idle > 2 min, a is most recent


def test_explicit_focus_reverts_to_auto_when_session_ends():
    st, fm = make()
    a = st.ingest(ev("UserPromptSubmit"), 0)
    b = st.ingest(ev("UserPromptSubmit", host="VM-1", sid="b", cwd="/x/Flip"), 1)
    fm.set_focus("Flip", 2)
    e = ev("SessionEnd", host="VM-1", sid="b", cwd="/x/Flip", reason="logout")
    st.ingest(e, 3)
    ch = fm.on_event(b, e, 3)
    assert fm.mode == "auto" and ch and ch.session is a


def test_heads_up_rate_limits():
    cfg = FocusCfg(heads_up_session_interval_s=300, heads_up_global_interval_s=30)
    st = SessionStore()
    hu = HeadsUp(cfg)
    focused = st.ingest(ev("UserPromptSubmit"), 0)
    other = st.ingest(ev("UserPromptSubmit", host="VM-2", sid="f", cwd="/c/Flipopolis"), 0)
    third = st.ingest(ev("UserPromptSubmit", host="VM-3", sid="t", cwd="/c/Notes"), 0)
    assert hu.check(focused, ev("Stop"), focused, 10) is None  # focused: robot animates instead
    assert hu.check(other, ev("PreToolUse"), focused, 10) is None  # not a waiting event
    assert hu.check(other, ev("Stop"), focused, 10) == "Heads up, Flipopolis on VM 2 is waiting for you."
    assert hu.check(other, ev("Notification"), focused, 20) is None  # per-session limit
    assert hu.check(third, ev("PermissionRequest"), focused, 20) is None  # global limit
    assert hu.check(third, ev("PermissionRequest"), focused, 41) == "Heads up, Notes on VM 3 needs a permission."
    assert hu.check(other, ev("Notification"), focused, 200) is None
    assert hu.check(other, ev("Notification"), focused, 311) == "Heads up, Flipopolis on VM 2 needs you."
    hu.cfg.heads_up = False
    assert hu.check(third, ev("Stop"), focused, 9999) is None


def test_prune_and_spoken():
    st = SessionStore(forget_ended_after_s=60, forget_idle_after_s=1000)
    a = st.ingest(ev("SessionEnd", reason="exit"), 0)
    st.ingest(ev("SessionStart", sid="z"), 0)
    assert a in st.prune(61)
    assert len(st.prune(2000)) == 1 and not st.sessions
    assert spoken("Job_Search on VM-2") == "Job Search on VM 2"
