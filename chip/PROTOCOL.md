# Chip: robot ↔ brain protocol (v1)

"Chip" is a Tiny Engineer with ears. The robot (ESP32-C3 firmware) streams its
microphone to a **brain** process on an always-on home PC. The brain does wake
word, speech-to-text, Claude, text-to-speech, task focus, range coaching and
idle personality, and is the only thing that drives the robot. Claude Code
sessions on any machine report hook events to the brain, not to the robot.

```
 VMs (Claude Code hooks) ──HTTP POST /events──►  BRAIN (home PC)  ──HTTP──► ROBOT
                                                     ▲                      │
                                                     └──── UDP mic audio ───┘
```

Everything below is the contract between the firmware (`src/`, `include/`)
and the brain (`chip/brain/`). Change both sides together.

## 1. New hardware (pins)

| Part | Signal | ESP32-C3 pin | Notes |
| --- | --- | --- | --- |
| INMP441 I2S mic | SCK | GP2 | shared with MAX98357A BCLK |
| | WS | GP3 | shared with MAX98357A LRC |
| | SD (mic data out) | **GP6** | I2S data in |
| | L/R | GND | mic on the **left** slot |
| | VDD / GND | 3V3 / GND | never 5 V |
| Mute button (momentary, to GND) | — | **GP7** | `INPUT_PULLUP`, active low |
| INA219 current sensor | SDA / SCL | GP0 / GP1 | same I2C bus as PCA9685 + OLED |
| | address | **0x41** | A0 jumper bridged (PCA9685 owns 0x40) |
| | VIN+ → VIN− | in series with the 5 V lead to the PCA9685 **V+** screw terminal | measures total servo current |

All three parts are optional at runtime: firmware must boot and behave exactly
like stock when a part is missing (no mic → stream silence or don't stream; no
INA219 at 0x41 → bump detection off, `current_ma` null; nothing on GP7 → never
muted by button).

## 2. Audio bus change

The ESP32-C3 has one I2S controller. Mic RX and speaker TX run **full duplex**
on it, sharing BCLK/WS. The INMP441 needs 64 BCLK per frame, so the bus moves
from 16-bit to **32-bit stereo slots** at the existing `SAMPLE_RATE` (22050 Hz).
Speaker writes put each 16-bit sample in the top half of its 32-bit slot. Mic
samples are 24-bit, MSB-aligned in the left 32-bit slot.

## 3. Mic stream (robot → brain, UDP)

* Destination: settings `brain_host` (IPv4 or hostname) and `brain_port`
  (default **47100**). Empty `brain_host` = streaming off.
* Only in Wi-Fi mode, only when not muted.
* Payload: 16-bit signed little-endian **mono** PCM at **22050 Hz**, 256 samples
  per packet (≈ 86 packets/s), after a 16-byte header:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | magic `CHP1` |
| 4 | 4 | `seq` uint32 LE, +1 per packet, wraps |
| 8 | 4 | `t_ms` uint32 LE, robot `millis()` of the first sample |
| 12 | 2 | `flags` uint16 LE (below) |
| 14 | 2 | `n` uint16 LE, sample count (0 for heartbeat) |

`flags`: bit0 `muted`, bit1 `speaker` (any WAV/`/play` audio is playing, or
ended < 300 ms ago), bit2 `moving` (any servo moving), bit3 `asleep`.

* While muted: **no audio**, one heartbeat packet (`n = 0`, `muted` set) per
  second so the brain shows the right state.
* The brain must discard audio with `speaker` set (Chip must not hear itself).

## 4. New / changed HTTP routes (robot)

All JSON, same auth rules as today (`Access::Configured` unless noted). Every
route goes into `kRoutes`, `docs/api.md`, `ui/src/views/ApiReference.tsx`.

### `GET /mic` / `POST /mic`
`{"muted": bool}` in, `{"ok":true,"muted":bool,"streaming":bool,"present":bool,"level":0..1}` out.
`present` = mic data seen (non-constant samples) since boot. `level` = recent RMS
(0..1). Mute state persists in NVS. The GP7 button toggles the same state.
Muted shows a visible indicator on the robot (OLED mark if feasible, otherwise
the RGB LED: dim amber when the animation would leave the LED off).

### `POST /pose`
Smooth free-form move, for idle personality and "look at you".
```json
{"pose":[0.3, null, null, null, -0.2], "ms": 600}
```
* `pose`: 5 entries `[head, neck, left, right, body]`, each −1..1 in the saved
  range (same pose space as animations) or `null` = leave that joint.
* `ms`: 100–5000, time to arrive (eased). Firmware still respects
  `SERVO_MAX_SPEED_DEG_S`.
* Stops the current animation (state becomes `none`), counts as activity
  (wakes from sleep like `/anim`). Returns when the move is *started*:
  `{"ok":true}`. 503 `busy` while `/play` holds the robot.

### New animation `listening` (`POST /anim?name=listening`)
Continuous. Head up toward the user, neck and body centred, hands at rest,
**then holds completely still** (no fidget, PWM released after the usual hold
delay) so servo noise stays off the mic. Eyes attentive/wide. Allowed as a
`/play/{name}` pose too. Same `continuous_timeout` rules as `talking`.

### `GET /ranges`
```json
{"ok":true,
 "saved_mins":[..5], "saved_maxs":[..5],
 "soft_mins":[..5],  "soft_maxs":[..5],
 "hard_mins":[..5],  "hard_maxs":[..5],
 "current_ma": 123.4, "sensor": true,
 "bump_threshold_ma": 650, "bumps":[ ... see below ... ]}
```
* `saved_*`: the NVS calibration (what animations use).
* `soft_*`: runtime limits after bumps (RAM only, start = saved). Animations and
  `/pose` are clamped to soft limits.
* `hard_*`: compile-time caps (in `include/servos.h`, 10–170°) that bound
  `POST /ranges` and `POST /ranges/explore`. They do **not** clamp the saved
  calibration at runtime (stock behaviour for ranges saved by the setup wizard).
* `current_ma` is `null` and `sensor` is `false` without an INA219 or while it
  stops answering (5 failed reads in a row; retried once a second).
* `bumps`: newest-first ring buffer (max 32) of
  `{"t_ms":..,"joint":0..4,"angle":deg,"dir":"min"|"max","current_ma":..,"anim":"typing","kind":"bump"|"stall"}`.
  `angle` is the commanded angle when the current first crossed the threshold.

### `POST /ranges`
`{"servo_mins":[..5],"servo_maxs":[..5]}` — saves new calibration **outside**
setup mode. Validation: each `hard_min ≤ min < max ≤ hard_max`, span ≥ 6°.
Resets soft limits to the new saved values. 400 with a reason otherwise.

### `POST /ranges/explore`
`{"joint":0..4,"dir":"min"|"max","speed":10}` (speed 5–30 °/s, default 10).
Blocking (like `/test/*`, at most ~14 s): stops animations, holds other joints,
learns the holding current, moves the joint to its saved limit, then slowly
toward the hard limit. The sweep ends at a bump, the hard limit, **30° past the
saved limit**, or 12 s, whichever comes first. Bump threshold while exploring:
`min(bump_threshold_ma, 400)`. Backs off 4° from first contact, returns
`{"ok":true,"joint":j,"dir":"max","stopped_at":deg,"reason":"bump"|"hard_limit"|"max_travel","safe":deg,"peak_ma":..}`.
`max_travel` = stopped by the 30° / 12 s bound with nothing felt.
Does **not** save; the brain decides. Errors: 409 `no current sensor` (no
INA219), 409 `current sensor not responding`, 409 `implausible servo current;
check INA219 VIN+/VIN- wiring` (holding current < 10 mA, negative, or > 1500 mA;
nothing moved), 500 `current sensor failed during explore` (reads failed
mid-run; the joint goes back to where it started), 500 `could not read a
steady servo current` (no baseline within 1 s of holding still; nothing moved).

### `POST /bumps/clear`
Empties the bump list and resets soft limits to saved. `{"ok":true}`.

### Settings (`GET`/`POST /settings`, immediate apply)
| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `brain_host` | string 0–63 | `""` | where to stream the mic |
| `brain_port` | int 1–65535 | 47100 | UDP port |
| `bump_threshold_ma` | int 0–3000 | 650 | 0 disables the reflex |
| `mic_shift` | int 8–20 | 14 | 16-bit sample = saturate(int32 slot >> `mic_shift`); lower = louder (6 dB per step) |

### `GET /health` additions
`"mic":{"present":bool,"muted":bool,"streaming":bool}`,
`"current_ma": number|null`, `"bumps": count`.

## 5. Bump reflex (firmware, every ≤ 10 ms)

1. Read INA219 current. "Moving" = motion commanded within the last 250 ms
   (a joint slewing, a target change, or a `/pose` in flight). Keep an idle
   baseline (EMA) per output state (powered / limp), learned only while nothing
   moves, armed after 10 still samples, never fed samples above
   `baseline + threshold/2` (a load between half and the full threshold is
   learned after 2 s). Pause 400 ms after released outputs switch back on.
2. A **bump** is current above `baseline + bump_threshold_ma` for **≥ 60 ms**
   while moving, or ≥ 2500 mA at once. A **stall** is the same excess for
   **≥ 300 ms** with powered servos and no motion.
3. On either: stop every joint where it is. Blame: for a bump, every joint that
   moved in the last 250 ms (travel direction); for a stall, only the single
   joint whose own motion ended within 650 ms, direction = its side of saved
   mid. The reflex's own stops/back-offs are never blamable. For each blamed
   joint take its commanded angle from when the current
   first crossed the threshold, move it back **4°** against its travel direction
   at normal speed (not past saved mid on an outward move), set its soft limit on
   that side to `angle − 2°` (never past saved mid), log an entry per joint
   (`kind` `bump`/`stall`), and put the animation in `none`. No certain blame
   = switch all servo outputs off (ignored, logged once, while `/servos/park`
   holds them).
4. Ignore events for 500 ms after one. Off when the INA219 stops answering.

The reflex never widens anything. Only `POST /ranges` (validated) can.

## 6. Hook events (any machine → brain, HTTP)

`POST http://<brain>:47101/events` with the raw Claude Code hook JSON plus:
```json
{"host":"VM-NAME","received_from":"chip-hook/1", "...hook fields...": "..."}
```
Fields the brain relies on: `hook_event_name`, `session_id`, `cwd`,
`tool_name`, `tool_input` (trimmed), `prompt` (UserPromptSubmit),
`source`/`reason` (SessionStart/End), `message` (Notification). The forwarder
(`chip/hooks/chip-hook.js`) never blocks Claude Code: 1.5 s timeout, always
exits 0.

Animation mapping is the same as `packages/tiny-engineer-cli/.../claude-code/map.js`,
applied by the brain only for the **focused** session.

## 7. Brain HTTP (home PC, port 47101)

| Route | Use |
| --- | --- |
| `POST /events` | hook intake (above) |
| `GET /status` | JSON: focus, sessions, mic state, last transcript, robot reachability |
| `POST /say` | `{"text":"..."}` make Chip speak (debug / scripts) |
| `POST /focus` | `{"session":"<id or label>"}` or `{"session":null}` for auto |
