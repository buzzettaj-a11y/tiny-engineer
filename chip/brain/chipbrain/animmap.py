"""Hook event -> robot animation. Port of
packages/tiny-engineer-cli/src/integrations/claude-code/map.js (keep in sync)."""

from __future__ import annotations

READING_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}
TYPING_TOOLS = {"Bash", "PowerShell", "Edit", "Write", "NotebookEdit"}

EVENT_ANIM = {
    "UserPromptSubmit": "reading",
    "SubagentStart": "thinking",
    "PostToolBatch": "thinking",
    "PostToolUseFailure": "error",
    "PreCompact": "thinking",
    "PermissionRequest": "attention",
    "PermissionDenied": "abort",
    "Notification": "attention",
    "Stop": "ring",
    "StopFailure": "error",
}

CONTINUOUS = {"typing", "reading", "thinking", "talking", "listening"}


def animation_for_event(event: dict) -> str | None:
    name = event.get("hook_event_name") if isinstance(event, dict) else None
    if not name:
        return None
    if name == "PreToolUse":
        tool = event.get("tool_name")
        if tool in READING_TOOLS:
            return "reading"
        if tool in TYPING_TOOLS:
            return "typing"
        return None
    # Only a fresh launch welcomes; resume/clear/compact wakes up.
    if name == "SessionStart":
        return "welcome" if event.get("source") == "startup" else "wakeup"
    # /clear and /resume end one session and start the next at once; the
    # SessionStart that follows owns the pose.
    if name == "SessionEnd":
        return None if event.get("reason") in ("clear", "resume") else "sleep"
    return EVENT_ANIM.get(name)
