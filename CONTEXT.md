# Chip

Chip is Joe's Tiny Engineer desk robot with ears: it listens for its name, talks back, keeps an eye on his Claude Code work, and feels when it bumps into things.

## Language

### The robot and its brain

**Chip**:
The robot itself, as a character with a name and a voice.
_Avoid_: the bot, Tiny (alone), the device

**Brain**:
The always-on program on Joe's home PC that hears, thinks and speaks for Chip. The robot only senses and moves.
_Avoid_: server, backend, host

**Local model**:
The AI that runs on the home PC through Ollama and answers first.
_Avoid_: Ollama (that's the runner, not the model), offline AI

**Claude**:
The cloud AI the brain hands harder questions to.
_Avoid_: the API, cloud model, Sonnet (that's one Claude model)

**Hand-off**:
The local model passing a question to Claude, said out loud ("let me ask Claude").
_Avoid_: escalation, fallback, routing

**Fallback**:
Claude answering because the local model is missing, too slow or broken, without being asked to.
_Avoid_: hand-off (that one is deliberate)

**Memory**:
What Joe has taught Chip to remember, kept between conversations.
_Avoid_: training, fine-tuning, history

**Teaching**:
Joe telling Chip something to keep in its memory ("Chip, remember…").
_Avoid_: training (that means changing the model itself)

### Listening

**Wake phrase**:
"Hey Chip", or "Chip" at the start of a sentence, which starts a conversation.
_Avoid_: hotword, trigger word

**Follow-up window**:
The few seconds after Chip answers when Joe can speak again without the wake phrase.
_Avoid_: session, listening mode

**Mute**:
Chip sends no sound from its mic. Set by the mute switch or the brain.
_Avoid_: privacy mode, sleep

### Watching Joe's work

**Session**:
One Claude Code conversation on one of Joe's machines, as seen through its hooks.
_Avoid_: job, task, agent

**Focus**:
The one session Chip follows and animates for. Chosen by Joe, or by Chip when Joe hasn't.
_Avoid_: active session, current job

**Heads-up**:
A one-line spoken note that a session outside the focus finished or needs Joe.
_Avoid_: notification, alert

### Touch and movement

**Bump**:
Chip pressing against something while moving, felt as extra servo current.
_Avoid_: collision, crash

**Stall**:
Chip pressing against something while holding still.
_Avoid_: jam, bump (that one is while moving)

**Saved range**:
The calibrated limits each joint normally moves within.
_Avoid_: calibration (that's the act of setting it), soft limit

**Soft limit**:
A tighter limit set after a bump, until the saved range is changed or bumps are cleared.
_Avoid_: saved range

**Hard cap**:
The outermost angle no saved range may ever go past.
_Avoid_: max, clamp

**Explore**:
Chip slowly moving one joint outward until it feels resistance, to measure a safe limit.
_Avoid_: calibrate, sweep test
