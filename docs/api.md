# HTTP API

JSON HTTP server on the ESP32-C3. Source: [`src/http/`](../src/http/). Every route and its access rule is listed in [`src/http/routes.cpp`](../src/http/routes.cpp).

Listens on **port 80** after STA Wi-Fi connects, or during setup AP mode at `http://192.168.4.1/`. Base URL is the board IP (OLED / serial) or `http://tiny-engineer.local` (mDNS, 2.4 GHz only).

Optional auth: when an `access_token` is configured in settings, all JSON API routes require `Authorization: Bearer <token>`. Empty token (default) means no auth. `GET /auth` is always public and reports whether auth is required. Missing/wrong token → **401** `{"ok":false,"error":"unauthorized"}`. HTML panel routes stay public (the UI prompts for the token). Replies are `Content-Type: application/json`. Routes that take parameters (except `POST /anim` and `/play`) read a JSON object body sent with `Content-Type: application/json`; query parameters or form fields on those routes → **400** `send a JSON body with Content-Type: application/json`, and a body that is not a JSON object → **400** `body must be a JSON object`. CORS: requests with an `Origin` header get `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers: Authorization, Content-Type` and `Access-Control-Allow-Methods: GET, POST, PUT, OPTIONS`; a preflight `OPTIONS` gets **200**.

When the robot is not yet provisioned (no connection mode chosen), control APIs (`/anim`, `/test/*`, `/play`, `/mic`, `/pose`, `/ranges`, `/ranges/explore`, `/bumps/clear`) return **503** `{"ok":false,"error":"not provisioned"}`. Setup routes (`/`, `/config`, `/auth`, `/health`, `/settings`, `/setup/servo`, `/setup/led`, `/setup/audio`, `/setup/oled`, `/servos/park`) stay available on the setup AP. Connection mode is `wifi` or `usb` (`link_mode` in settings/auth/health). USB-only mode disables Wi‑Fi station and the HTTP API after reboot; control then uses the USB serial protocol ([serial.md](serial.md)).

If boot WiFi credentials are missing, the device opens setup AP mode (`TinyEngineer-XXXX`) and serves a five-step setup wizard at `/config` (servo hub: guided calibration / skip / assembly centering, then OLED orientation, RGB LED, speaker test, then connection). If saved credentials fail, it reopens setup AP mode. The setup AP acts as a captive portal: DHCP sends the portal URL (option 114) and DNS answers every name with the AP IP, so a phone that joins the network opens the wizard on its own. Hardware tests: [`docs/hardware/testing.md`](hardware/testing.md). How to wire this API into AI tools: [`integration.md`](integration.md).

Firmware answers mDNS A and AAAA (IPv6 link-local) so macOS does not wait 2–3s on a missing AAAA. If a client still pauses on the hostname, force IPv4 (`curl -4`). The IP on the OLED skips DNS entirely.

## Endpoints

### `GET /`

HTML endpoint index. Lists all routes plus supported parameters for `/anim`, `/settings`, `/play`, `/mic`, `/pose`, `/ranges`, `/ranges/explore`, `/test/servo`, `/setup/servo`, `/setup/led`, `/setup/audio`, `/setup/oled`, and `/servos/park`. Safe, no hardware side effects. Always public (no Bearer required).

Open in a browser:

```bash
curl http://tiny-engineer.local/
```

Returns `Content-Type: text/html; charset=utf-8`, gzip-encoded. The page is built from [`ui/`](../ui/) with Vite and embedded in the firmware, so it always matches the API the firmware serves.

### `GET /auth`

Whether API auth is required. Always public. Safe, no hardware side effects. Does not reveal the token.

```bash
curl http://tiny-engineer.local/auth
```

```json
{ "ok": true, "version": "v0.3.0", "required": false, "wifi_configured": true, "link_mode": "wifi", "provisioning": false }
```

| Field | Meaning |
| --- | --- |
| `ok` | Always `true` on this route |
| `version` | Firmware version, same as `GET /health` |
| `required` | `true` when a non-empty access token is configured |
| `wifi_configured` | `true` when WiFi credentials are saved in NVS |
| `link_mode` | `""` unprovisioned, `"wifi"`, or `"usb"` |
| `provisioning` | `true` when the setup AP is active |

### `GET /health`

Health. Safe, no hardware side effects. Uses live `WiFi.status()`, not the boot-time flag. Requires Bearer when auth is enabled.

```bash
curl http://tiny-engineer.local/health
curl -H "Authorization: Bearer YOUR_TOKEN" http://tiny-engineer.local/health
```

```json
{
  "ok": true,
  "version": "v0.1.0-82-g7c79b83",
  "uptime_ms": 12345,
  "free_heap": 120000,
  "heap_size": 320000,
  "cpu_temp_c": 41.2,
  "wifi": {
    "connected": true,
    "ip": "192.168.1.10",
    "rssi": -42,
    "hostname": "tiny-engineer.local"
  },
  "wifi_configured": true,
  "provisioning": false,
  "setup_ap_ssid": "",
  "setup_ap_ip": "",
  "oled": true,
  "mic": { "present": true, "muted": false, "streaming": true },
  "current_ma": 182.4,
  "bumps": 0
}
```

| Field | Meaning |
| --- | --- |
| `ok` | Always `true` on this route |
| `version` | Firmware build string from `git describe` at compile time (`v0.1.0` on a tagged release, `v0.1.0-<n>-g<sha>[-dirty]` for a dev build, or `unknown`) |
| `uptime_ms` | `millis()` since boot |
| `free_heap` | `ESP.getFreeHeap()` — bytes available for allocation |
| `heap_size` | `ESP.getHeapSize()` — total heap bytes; usage % = `(1 - free_heap / heap_size) × 100` |
| `cpu_temp_c` | ESP32-C3 on-die temperature in °C (not ambient); `null` if the sensor is unavailable |
| `wifi.connected` | `WiFi.status() == WL_CONNECTED` |
| `wifi.ip` | STA IPv4, or `""` if down |
| `wifi.rssi` | dBm, or `0` if down |
| `wifi.hostname` | mDNS name from settings (`{hostname}.local`, default `tiny-engineer.local`) |
| `wifi_configured` | `true` when WiFi credentials are saved in NVS |
| `provisioning` | `true` when setup AP mode is active |
| `setup_ap_ssid` | Setup AP SSID when `provisioning` is true, else `""` |
| `setup_ap_ip` | Setup AP IP when `provisioning` is true (usually `192.168.4.1`), else `""` |
| `oled` | SSD1306 probed and initialized |
| `mic.present` | INMP441 data seen (non-constant samples) since boot; `false` without a mic |
| `mic.muted` | Mic muted (GP7 button or switch, or [`POST /mic`](#post-mic)) |
| `mic.streaming` | UDP mic packets are reaching the network stack ([Mic stream](#mic-stream-udp)) |
| `current_ma` | Servo-rail current from the optional INA219 (mA, one decimal), or `null` without the sensor or while it stops answering (5 failed reads in a row; retried once a second) |
| `bumps` | Entries in the bump log ([`GET /ranges`](#get-ranges)) |

### `GET /settings`

Persistent settings from NVS. Safe, no hardware side effects. Developer guide for adding settings: [`settings.md`](settings.md).

```bash
curl http://tiny-engineer.local/settings
```

```json
{
  "ok": true,
  "sleep_timeout": 10,
  "hostname": "tiny-engineer",
  "volume": 70,
  "welcome": true,
  "serial_log": false,
  "continuous_timeout": 5,
  "loading": "progress",
  "eyes_style": "classic",
  "access_token_set": false,
  "link_mode": "wifi",
  "wifi_configured": true,
  "servo_mins": [80, 80, 80, 80, 80],
  "servo_maxs": [100, 100, 100, 100, 100],
  "rgb_order": "GRB",
  "oled_rotate_180": false,
  "brain_host": "192.168.1.20",
  "brain_port": 47100,
  "bump_threshold_ma": 650,
  "mic_shift": 14,
  "mute_switch": false,
  "wifi_networks": ["MyNetwork", "Office"]
}
```

| Field | Meaning |
| --- | --- |
| `sleep_timeout` | Minutes of continuous animation `none` before OLED blanks (default **10**). Any non-idle animation keeps the device awake and restarts the idle clock when returning to `none`. Other APIs do not affect the timer. |
| `hostname` | DHCP/mDNS label without `.local` (default **`tiny-engineer`**) |
| `volume` | Speaker gain percent for tones and WAV playback (default **70**) |
| `welcome` | Play welcome animation on boot when Wi-Fi connects (default **true**). Also enables head/neck motion during `sleep_inertia` loading |
| `serial_log` | USB serial debug logging (default **false**). When off, diagnostic `Serial` output is suppressed |
| `continuous_timeout` | Minutes a continuous animation (`typing` / `reading` / `thinking` / `talking`) may run before the firmware switches to `attention` then idle (default **5**) |
| `loading` | Boot loading screen: `progress` (bar + large IP for 3 s) or `sleep_inertia` (eyes wake; no bar/IP). Default **`progress`**. Applies on **next reboot** |
| `eyes_style` | OLED eyes look: `classic` (procedural rounded eyes), `kaomoji` (animated faces), `cover` (full-half bars for an eye-hole mask), or `dots` (12×12 circles). Default **`classic`**. Applies **immediately**. Classic, cover, and dots sequences are described under [`POST /anim`](#post-anim); kaomoji face mapping is under [Kaomoji eyes](#kaomoji-eyes); cover drawing is under [Cover eyes](#cover-eyes); dots drawing is under [Dots eyes](#dots-eyes) |
| `access_token_set` | `true` when a non-empty access token is stored (secret itself is never returned) |
| `link_mode` | `""` unprovisioned, `"wifi"`, or `"usb"` (connection mode; see [`serial.md`](serial.md)) |
| `wifi_configured` | `true` when at least one WiFi network is saved |
| `wifi_networks` | Saved network names (up to 5), in saved order. Passwords are never returned. At boot the robot scans and tries the visible saved networks strongest first, then the rest in this order (hidden SSIDs do not show in a scan). Change the list with [`PUT /settings/wifi`](#put-settingswifi) |
| `servo_mins` | Per-joint safe minimum degrees `[head, neck, left, right, body]` (stock defaults **80, 80, 80, 80, 80**) |
| `servo_maxs` | Per-joint safe maximum degrees (stock defaults **100, 100, 100, 100, 100**). Each max must be greater than the matching min |
| `rgb_order` | Onboard WS2812 wire-byte order (`RGB`, `RBG`, `GRB`, `GBR`, `BRG`, `BGR`). Default **`GRB`**. Survives factory reset |
| `oled_rotate_180` | OLED 180° rotation (`true` → `setRotation(2)`). Default **`false`**. Setup AP only. Survives factory reset |
| `brain_host` | Where the mic streams: IPv4 or hostname of the Chip brain. Default **`""`** (streaming off). See [Mic stream](#mic-stream-udp) |
| `brain_port` | UDP port for the mic stream. Default **47100** |
| `bump_threshold_ma` | Bump reflex: servo-rail current above the idle baseline that counts as a bump (after 60 ms while moving). Default **650**; `0` disables the reflex. Needs the INA219 ([Bump reflex](#bump-reflex)) |
| `mic_shift` | Mic gain: 16-bit sample = saturate(32-bit I2S slot >> `mic_shift`). Lower = louder, 6 dB per step. Default **14** |
| `mute_switch` | What is wired to GP7: `false` = momentary mute button, each press toggles mute (default); `true` = maintained on/off switch whose position **is** the mute state: closed to GND ("I") = listening, open ("O", or a broken wire) = muted. In switch mode `POST /mic` is refused. Applies immediately. Survives factory reset. See [`POST /mic`](#post-mic) |
| `reboot_required` | `true` once the saved `hostname`, `loading` or WiFi networks differ from what this boot started with; those apply only after a reboot |

### `POST /settings`

Update one or more settings from a JSON object body. Values are written to NVS. `sleep_timeout`, `volume`, `welcome`, `serial_log`, `continuous_timeout`, `eyes_style`, `access_token`, `brain_host`, `brain_port`, `bump_threshold_ma`, `mic_shift`, and `mute_switch` apply immediately; a changed `hostname` or `loading` takes effect on the **next reboot**. WiFi networks are set with [`PUT /settings/wifi`](#put-settingswifi). Servo ranges (`servo_mins` + `servo_maxs`), RGB LED mapping (`rgb_order`) and OLED rotation (`oled_rotate_180`) are **setup AP only** and apply immediately. Requires Bearer when auth is enabled.

```bash
curl -X POST "http://tiny-engineer.local/settings" \
  -H "Content-Type: application/json" \
  -d '{"volume":40,"welcome":false,"eyes_style":"kaomoji"}'
curl -X POST "http://tiny-engineer.local/settings" \
  -H "Content-Type: application/json" \
  -d '{"access_token":""}'
curl -X POST "http://192.168.4.1/settings" \
  -H "Content-Type: application/json" \
  -d '{"servo_mins":[80,80,80,80,80],"servo_maxs":[100,100,100,100,100],"rgb_order":"GRB","oled_rotate_180":true}'
curl -X POST "http://tiny-engineer.local/settings" \
  -H "Content-Type: application/json" \
  -d '{"brain_host":"192.168.1.20","brain_port":47100,"bump_threshold_ma":650,"mic_shift":14}'
```

```json
{
  "ok": true,
  "sleep_timeout": 1,
  "hostname": "desk-bot",
  "volume": 70,
  "welcome": true,
  "serial_log": false,
  "continuous_timeout": 5,
  "loading": "progress",
  "eyes_style": "classic",
  "access_token_set": true,
  "link_mode": "wifi",
  "wifi_configured": true,
  "servo_mins": [80, 80, 80, 80, 80],
  "servo_maxs": [100, 100, 100, 100, 100],
  "rgb_order": "GRB",
  "oled_rotate_180": false,
  "brain_host": "",
  "brain_port": 47100,
  "bump_threshold_ma": 650,
  "mic_shift": 14,
  "mute_switch": false,
  "wifi_networks": ["MyNetwork"],
  "reboot_required": true
}
```

| Field | Type | Range |
| --- | --- | --- |
| `sleep_timeout` | integer | 1–1440 minutes (positive) |
| `hostname` | string | 1–31 chars, `[A-Za-z0-9-]`, not starting/ending with `-` |
| `volume` | integer | 0–100 percent |
| `welcome` | boolean | Boot welcome animation |
| `serial_log` | boolean | USB serial debug logging |
| `continuous_timeout` | integer | 1–1440 minutes (positive) |
| `loading` | string | `progress` or `sleep_inertia` |
| `eyes_style` | string | `classic`, `kaomoji`, `cover`, or `dots` |
| `access_token` | string | 0–64 printable ASCII, no space at either end; empty string clears the token and disables auth |
| `link_mode` | string | `wifi` or `usb`; setup Connection step uses `usb` for USB-only; Wi-Fi mode is usually set when saving networks via [`PUT /settings/wifi`](#put-settingswifi) |
| `servo_mins` | integer array | Five integers `0`–`180` (`head, neck, left, right, body`); setup AP only; must be sent with `servo_maxs` |
| `servo_maxs` | integer array | Five integers `0`–`180`; setup AP only; each value must be greater than the matching min |
| `rgb_order` | string | `RGB`, `RBG`, `GRB`, `GBR`, `BRG`, or `BGR`; setup AP only; default `GRB` |
| `oled_rotate_180` | boolean | Setup AP only; default `false` |
| `brain_host` | string | 0–63 chars, `[A-Za-z0-9.-]` (hostname or IPv4), not starting with `-` or `.` nor ending with `-`; empty turns the mic stream off; default empty |
| `brain_port` | integer | 1–65535; default `47100` |
| `bump_threshold_ma` | integer | 0–3000 mA; `0` disables the bump reflex; default `650` |
| `mic_shift` | integer | 8–20; default `14` |
| `mute_switch` | boolean | `false` = GP7 mute button (each press toggles), `true` = GP7 on/off switch (open = muted); default `false` |

Leave out a field to keep its saved value. An empty object → **400** `send at least one setting`. A field this route does not know → **400** `unknown field <field>`. A field with the wrong type or out of range → **400** `invalid <field>` (servo ranges that overlap or exceed 180: `invalid servo_mins or servo_maxs`). Only one of `servo_mins` / `servo_maxs` → **400** `servo_mins and servo_maxs go together`. On any 400, nothing is saved. Outside setup AP mode, servo ranges → **400** `servo setup only in AP mode`, `rgb_order` → **400** `rgb setup only in AP mode`, `oled_rotate_180` → **400** `oled setup only in AP mode`. Replies carry `reboot_required` as in `GET /settings`.

### `PUT /settings/wifi`

Replace the saved WiFi networks with the list in the JSON body. The robot checks the whole list, then saves it in one write. A network without `password` keeps its saved password; a new network needs one (`""` for an open network). Order is boot priority for networks a scan misses. Requires Bearer when auth is enabled. Same JSON shape as `GET /settings`.

Outside setup AP mode the list is saved **without a test** and applies at the next boot; the current connection stays up. In setup AP mode the robot **tests the first network** before saving; on success it joins that network and the reply adds `wifi_connect_success`, `wifi_ip`, and `wifi_hostname`. The test registers mDNS under the saved hostname, so set `hostname` with `POST /settings` first.

```bash
curl -X PUT "http://tiny-engineer.local/settings/wifi" \
  -H "Content-Type: application/json" \
  -d '[{"ssid":"Office","password":"secret"},{"ssid":"Home"},{"ssid":"Cafe","password":""}]'
```

| Field | Type | Range |
| --- | --- | --- |
| `ssid` | string | 1–32 chars; unique in the list |
| `password` | string | 0–63 chars; omit to keep the saved password of a saved `ssid` |

The list must hold 1 to 5 networks, so the robot always has one to try; factory reset clears WiFi. No JSON body → **400** `send a JSON body with Content-Type: application/json`. Body not a JSON array → **400** `body must be a JSON array`. An entry that is not an object, or has a non-string `ssid` or `password` (including `null`) → **400** `each network needs a string ssid and an optional string password`. Empty → **400** `send at least one wifi network`. New SSID without `password` → **400** `password required for a new network`. Bad length, the same SSID twice, or more than 5 networks → **400** `invalid ssid or password, duplicate ssid, or more than 5 networks`. WiFi test failure in setup AP mode → **400** with an error such as `SSID not found`, `Auth failed`, or `Timeout`.

### `POST /settings/reset`

Factory reset: clears the NVS `te` namespace and restores settings to compile-time defaults and clears every saved WiFi network. **Servo min/max, RGB LED mapping (`rgb_order`), OLED rotation (`oled_rotate_180`), and the mute input mode (`mute_switch`) are kept.** Internal last-pose blob (`spose`) is cleared/reset with defaults. The Chip settings (`brain_host`, `brain_port`, `bump_threshold_ma`, `mic_shift`) return to their defaults, and the saved mic mute (`mic_mute`) is cleared: the mic stays as it is until the next boot, then starts unmuted. After reset, power-cycle (or press reset) so the device reopens setup AP mode and WiFi can be configured again; the servo, screen, and LED steps are pre-filled with the saved values and can be retuned. Until reboot, credentials are cleared in NVS but the radio may still be on the previous STA network. No body. Requires Bearer when auth is enabled. Same JSON shape as `GET /settings`.

The Config web UI clears the browser-stored Bearer token and shows a one-shot gate: power-cycle the device, then join the robot WiFi shown on the OLED and open the setup wizard. It does not poll for reconnect.

```bash
curl -X POST "http://tiny-engineer.local/settings/reset"
```

```json
{
  "ok": true,
  "sleep_timeout": 10,
  "hostname": "tiny-engineer",
  "volume": 70,
  "welcome": true,
  "serial_log": false,
  "continuous_timeout": 5,
  "loading": "progress",
  "eyes_style": "classic",
  "access_token_set": false,
  "wifi_configured": false,
  "servo_mins": [80, 80, 80, 80, 80],
  "servo_maxs": [100, 100, 100, 100, 100],
  "rgb_order": "GRB",
  "oled_rotate_180": false,
  "brain_host": "",
  "brain_port": 47100,
  "bump_threshold_ma": 650,
  "mic_shift": 14,
  "mute_switch": false,
  "wifi_networks": [],
  "reboot_required": true
}
```

NVS write failure → **400** `{"ok":false,"error":"factory reset failed"}`.

### `POST /test/audio`

Plays 500 / 700 / 1000 Hz on the MAX98357A (`runSoundTest()`).

```bash
curl -X POST http://tiny-engineer.local/test/audio
```

```json
{ "ok": true, "test": "audio" }
```

### `POST /test/audio/bell`

Plays `/bell.wav` from LittleFS (`playBell()`). Git source is [`assets/bell.wav`](../assets/bell.wav) (44100 Hz mono PCM); `pio run` downsamples it to 22050 Hz into `data/` for the filesystem image. After changing the asset, upload with `pio run -t upload` or `pio run -t uploadfs`. Do not edit `data/` by hand — the next build overwrites it.

```bash
curl -X POST http://tiny-engineer.local/test/audio/bell
```

```json
{ "ok": true, "test": "bell" }
```

If the WAV is missing or unreadable, returns **500** with `{"ok":false,"error":"bell playback failed"}`.

### `POST /test/screen`

OLED demo: title, `HELLO`, X in a box (`runOledTest()`). No-op if the panel is missing.

```bash
curl -X POST http://tiny-engineer.local/test/screen
```

```json
{ "ok": true, "test": "screen" }
```

### `POST /test/movement`

All five servos: per-joint mid → +0.5 → −0.5 → mid of the saved min/max (`runServoTest()`). Needs a strong 5 V supply — see [power.md](hardware/power.md).

```bash
curl -X POST http://tiny-engineer.local/test/movement
```

```json
{ "ok": true, "test": "movement" }
```

### `POST /test/led`

Onboard WS2812 on GPIO10: R → G → B → white → off (`runRgbTest()`), then fades back to the current animation color (see [RGB LED](#rgb-led)).

```bash
curl -X POST http://tiny-engineer.local/test/led
```

```json
{ "ok": true, "test": "led" }
```

### `POST /setup/servo`

Setup AP only. Slowly move one servo, or all five to 90°, using the full **0–180°** electrical range (no saved safe-range clamp). ~25°/s (`SERVO_CALIB_SPEED_DEG_S`). Used by the setup wizard for seating printed parts at 90° and for range finding. Handler blocks until the move finishes. Does **not** require Wi-Fi credentials. Outside provisioning → **400** `setup only in AP mode`.

JSON body: either `{"all": 90}`, or `index` + `angle`.

| Field | Type | Range |
| --- | --- | --- |
| `all` | integer | `90` (every joint to 90°) |
| `index` | integer | `0`–`4` (required with `angle` when `all` is omitted) |
| `angle` | number | `0`–`180` |

```bash
curl -X POST "http://192.168.4.1/setup/servo" -H "Content-Type: application/json" -d '{"all":90}'
curl -X POST "http://192.168.4.1/setup/servo" -H "Content-Type: application/json" -d '{"index":0,"angle":90}'
```

```json
{ "ok": true, "setup": "servo", "index": 0, "angle": 90 }
```

Wrong fields return **400** and do not move any servo:

| `error` | When |
| --- | --- |
| `setup only in AP mode` | Not in setup AP |
| `invalid all` | `all` present but not `90` |
| `invalid index` | `index` missing, not an integer, or outside `0`–`4` |
| `invalid angle` | `angle` missing, not a number, or outside `0`–`180` |

After STA is up, use `POST /test/servo` (clamped to saved min/max).

### `POST /setup/led`

Setup AP only. Holds the LED so provisioning dim-blue does not clobber the preview. Does **not** require Wi-Fi credentials. Outside provisioning → **400** `setup only in AP mode`.

Logical **color** (`R` / `G` / `B`) lights firmware red/green/blue using the saved `rgb_order`, or the `rgb_order` field if sent (wizard remap preview). Wire **byte** lights that WS2812 channel order-independent (`LED_COLOR_ORDER_RGB`, that channel 255). An empty body (neither `color` nor `byte`) releases the hold and restores dim blue. Do not send `color` and `byte` together.

| Field | Type | Range |
| --- | --- | --- |
| `color` | string | `R`, `G`, or `B` (logical; uses saved or `rgb_order`) |
| `rgb_order` | string | `RGB`, `RBG`, `GRB`, `GBR`, `BRG`, or `BGR` (optional with `color`) |
| `byte` | integer | `0`, `1`, or `2` |

```bash
curl -X POST "http://192.168.4.1/setup/led" -H "Content-Type: application/json" -d '{"color":"R","rgb_order":"GRB"}'
curl -X POST "http://192.168.4.1/setup/led" -H "Content-Type: application/json" -d '{"byte":0}'
curl -X POST "http://192.168.4.1/setup/led"
```

```json
{ "ok": true, "setup": "led", "color": "R" }
```

Wrong fields return **400** and do not change the hold:

| `error` | When |
| --- | --- |
| `setup only in AP mode` | Not in setup AP |
| `color and byte conflict` | Both `color` and `byte` present |
| `invalid color` | `color` not `R`, `G`, or `B` |
| `invalid rgb_order` | `rgb_order` present but not a valid permutation |
| `invalid byte` | `byte` not an integer `0`–`2` |

Save the labeled permutation with `POST /settings` `{"rgb_order":"GRB"}` (setup AP only). The saved order applies immediately to animation colors, boot status, halt blinks, and `POST /test/led`.

### `POST /setup/audio`

Setup AP only. Plays `welcome.wav` from LittleFS (`playWelcome()`, ~2.7 s). Same speaker gain as settings `volume`. No body. Does **not** require Wi-Fi credentials. Handler blocks until playback finishes. Outside provisioning → **400** `setup only in AP mode`. Missing/unreadable WAV → **500** `welcome playback failed`.

```bash
curl -X POST "http://192.168.4.1/setup/audio"
```

```json
{ "ok": true, "setup": "audio" }
```

After STA is up, use `POST /test/audio/bell` (or `/test/audio` for tones).

### `POST /setup/oled`

Setup AP only. Preview OLED 180° rotation without saving. Does **not** require Wi-Fi credentials. Outside provisioning → **400** `setup only in AP mode`.

`{"rotate_180": true}` applies `setRotation(2)` and draws `THIS WAY UP`. `{"rotate_180": false}` applies `setRotation(0)` and draws the same test. An empty body restores the saved rotation and the provisioning join-AP text.

| Field | Type | Range |
| --- | --- | --- |
| `rotate_180` | boolean | Leave out to restore the provisioning text |

```bash
curl -X POST "http://192.168.4.1/setup/oled" -H "Content-Type: application/json" -d '{"rotate_180":true}'
curl -X POST "http://192.168.4.1/setup/oled"
```

```json
{ "ok": true, "setup": "oled", "rotate_180": true }
```

Wrong fields return **400**:

| `error` | When |
| --- | --- |
| `setup only in AP mode` | Not in setup AP |
| `invalid rotate_180` | `rotate_180` present but not a boolean |

Save with `POST /settings` `{"oled_rotate_180":true}` (setup AP only). The saved value applies immediately and on the next boot.

### `POST /servos/park`

Flash-safe park: stop the current animation, smoothly move every joint to its calibrated mid, persist the pose to NVS, and keep PWM enabled until the next reboot (skips idle limp). No body. Requires Bearer when auth is enabled. Available in setup AP and after provisioning (does not require a chosen connection mode).

```bash
curl -X POST "http://tiny-engineer.local/servos/park"
tiny-engineer park --transport serial
```

```json
{ "ok": true, "park": "servos", "hold": true }
```

Pose NVS write failure → **400** `{"ok":false,"error":"pose save failed"}` (PWM hold is still armed). Details: [hardware/servos.md](hardware/servos.md#flash-safe-park).

### `POST /test/servo`

Smoothly move one servo to an angle at **~140°/s** (`SERVO_MAX_SPEED_DEG_S`) from its last commanded position. JSON body required. Handler blocks until the move finishes.

| Field | Type | Range |
| --- | --- | --- |
| `index` | integer | `0`–`4` |
| `angle` | number | `0`–`180` |

```bash
curl -X POST "http://tiny-engineer.local/test/servo" -H "Content-Type: application/json" -d '{"index":0,"angle":90}'
```

```json
{ "ok": true, "test": "servo", "index": 0, "angle": 90 }
```

Wrong fields return **400** and do not move any servo:

| `error` | When |
| --- | --- |
| `invalid index` | `index` missing, not an integer, or outside `0`–`4` |
| `invalid angle` | `angle` missing, not a number, or outside `0`–`180` |

Assembled robot: firmware clamps to the soft limits — the NVS min/max from setup, narrowed by bumps (see [`GET /ranges`](#get-ranges); stock defaults in [robot-movement.md](robot-movement.md)); this route still accepts 0–180 and clamps. For unclamped assembly moves, use setup AP `POST /setup/servo`.

### `GET /anim`

Current animation name. Default at boot: `none`. No hardware side effects.

```bash
curl http://tiny-engineer.local/anim
```

```json
{ "ok": true, "animation": "none" }
```

| Field | Meaning |
| --- | --- |
| `animation` | `none`, `typing`, `reading`, `thinking`, `talking`, `listening`, `ring`, `welcome`, `attention`, `error`, `abort`, `dead`, `wakeup`, or `sleep` |

### `POST /anim`

Request an animation switch. Each animation runs at least **1s**; if the current one is shorter than that, the switch waits until the hold ends. Rapid posts during the hold keep only the **latest** request. Response `animation` is the currently playing name (may still be the old one until the hold ends). Hand moves use speed-limited servo wrappers.

| Param | Type | Values |
| --- | --- | --- |
| `name` | string | `none`, `typing`, `reading`, `thinking`, `talking`, `listening`, `ring`, `welcome`, `attention`, `error`, `abort`, `dead`, `wakeup`, `sleep` |

```bash
curl -X POST "http://tiny-engineer.local/anim?name=typing"
curl -X POST "http://tiny-engineer.local/anim?name=reading"
curl -X POST "http://tiny-engineer.local/anim?name=thinking"
curl -X POST "http://tiny-engineer.local/anim?name=talking"
curl -X POST "http://tiny-engineer.local/anim?name=listening"
curl -X POST "http://tiny-engineer.local/anim?name=ring"
curl -X POST "http://tiny-engineer.local/anim?name=welcome"
curl -X POST "http://tiny-engineer.local/anim?name=wakeup"
curl -X POST "http://tiny-engineer.local/anim?name=sleep"
curl -X POST "http://tiny-engineer.local/anim?name=attention"
curl -X POST "http://tiny-engineer.local/anim?name=error"
curl -X POST "http://tiny-engineer.local/anim?name=abort"
curl -X POST "http://tiny-engineer.local/anim?name=dead"
curl -X POST "http://tiny-engineer.local/anim?name=none"
```

```json
{ "ok": true, "animation": "typing" }
```

Eye detail below for `eyes_style=classic` (default). With `eyes_style=cover`, the same sequences drive full-half bars ([Cover eyes](#cover-eyes)). With `eyes_style=dots`, the same sequences drive 12×12 circles ([Dots eyes](#dots-eyes)). With `eyes_style=kaomoji`, faces follow [Kaomoji eyes](#kaomoji-eyes) instead of procedural blinks, glances, flicker, or X eyes. Servo/audio behavior is the same for every style.

Durations and quoted lines below are the stock clips in [`assets/`](../assets/). A filesystem built with `custom_audio_mod` can replace some of those WAVs. Phrase timing then follows that clip's `.cue` file on LittleFS (`/welcome.cue` and the same pattern for `attention`, `error`, `abort`, and `dead`). See [flash.md](flash.md).

| `name` | Behavior |
| --- | --- |
| `none` | Head/neck/body → mid; hands down (right `min`, left `max` — inverted scales). After all joints still for 2 s, PCA9685 PWM is full-off (servos limp; head may droop). Setup AP does not release PWM. |
| `typing` | **Continuous.** Alternating hands with randomness (15° band from hand limits). Head nods slowly on lowest 10° of head limits. Body sways ±5° around mid; neck counters opposite so head stays put. Runs until replaced, or until `continuous_timeout` triggers `attention`. Same-name re-`POST` refreshes that timeout without restarting motion. |
| `reading` | **Continuous.** Hands/body park as in `none`. Head nods on same lowest 10° band as typing, but slower. Neck sweeps ±10° around mid; right (angle down) slower than left (angle up). Occasional right-hand down-arrow bursts (1–3 presses, same 15° band as typing). Runs until replaced, or until `continuous_timeout` triggers `attention`. Same-name re-`POST` refreshes that timeout without restarting motion. |
| `thinking` | **Continuous.** Hands/body park as in `none`. Head (pitch) and neck (yaw) ease from the current pose into thinking poses (up + slight left/right). Move → pause → optional micro-adjust (sometimes chained) → pause; nearby pose drift with occasional larger shifts after ~2.2 s, more often over time. Axes stagger start/duration; no periodic sway. Runs until replaced, or until `continuous_timeout` triggers `attention`. Same-name re-`POST` refreshes that timeout without restarting motion. |
| `talking` | **Continuous.** Speaking fidget with no sound of its own. Also the default pose for [`POST /play`](#post-play). Via `/anim`, the head faces the user just above mid with soft random nods, slow neck drift, occasional small torso shifts, and occasional right-hand lifts. During `/play` with pose `talking`, motion follows speech energy from the PCM stream (no extra metadata): emphasis triggers nods, silence settles the pose, phrase starts may raise the hand or shift the torso (not both). **Classic eyes:** a little taller than idle; glances away when ambient or quiet, gaze locked on the listener while speech energy is high. Runs until replaced, or until `continuous_timeout` triggers `attention`. Same-name re-`POST` refreshes that timeout without restarting motion. |
| `listening` | **Continuous.** Head up toward the user (`+2/7` of the head range, like `welcome`), neck and body centred, hands at rest (right `min`, left `max`), then **holds completely still**: no fidget, and PWM goes full-off after all joints are still for 2 s (same as `none`) so servo noise stays off the mic. **Classic eyes:** taller than idle and lifted a pixel, gaze held on the user with a rare one-pixel drift. LED off. Allowed as a [`/play/{name}`](#post-play) pose. Runs until replaced, or until `continuous_timeout` triggers `attention`. Same-name re-`POST` refreshes that timeout without restarting motion. |
| `ring` | **One-shot** service-bell gesture; does not loop. Wind-up (body `min`, neck mid, head mid+10°, both hands `max`) → fast right-hand strike to `min+5°` with head to `min` → plays `bell.wav` once on strike (LittleFS; same `uploadfs` requirement as `/test/audio/bell`) → slower bounce to `min+20°` → return to `none` pose and stop. After completion, `GET /anim` reports `none`. |
| `welcome` | **One-shot** hello gesture synced to `welcome.wav` (~2.7 s). Right hand raises during "Hello, human.", holds through the pause, wiggles during "What are we building today?", then lowers. Head nods to mid+10° and returns. Plays automatically after successful Wi-Fi connect at boot; also via API. Requires `welcome.wav` on LittleFS (same `uploadfs` flow as `bell.wav`). After completion, `GET /anim` reports `none`. |
| `attention` | Friendly input-request gesture synced to `attention.wav` (~3.0 s, "pst... human.... you might want to take a look"). Moves into a calm prompt pose first (centered body/neck, head slightly up, right hand raised partway), waits until all servos stop, then plays audio with light neck/head/hand motion during playback (whisper hold → lean toward user → glance/point on "take a look"). **Classic eyes:** phased blink/look cues during audio. After audio ends (or if audio fails to start), holds a gentle waiting loop for **1 minute** (soft head/neck drifts plus occasional slight right-hand waves), then returns to `none`. Requires `attention.wav` on LittleFS (same `uploadfs` flow as `bell.wav`). |
| `error` | Critical task-obstacle gesture synced to `error.wav` (~2.2 s, "Uh-oh. Human, we have a problem."). Moves into an obstacle-presenting pose first (body/neck angled toward the task, head concerned/down, right hand presenting the blocker, left hand indicating task area), waits until the pose settles, then plays audio with small nervous head/neck glances during playback. After audio ends (or if audio fails to start), holds a subtle blocked loop for **1 minute**, then returns to `none`. Requires `error.wav` on LittleFS (same `uploadfs` flow as `bell.wav`). |
| `abort` | **One-shot** resigned abort gesture synced to `abort.wav` (~2.5 s, "Fine. I didn't want to finish it anyway."). Raises both hands, lifts the head, and twists the neck sideways before audio starts. During playback it shrugs, dips the head, and adds a dismissive side twist. **Classic eyes:** matching glances/squints. Requires `abort.wav` on LittleFS (same `uploadfs` flow as `bell.wav`). After completion, returns to `none` pose and `GET /anim` reports `none`. |
| `dead` | **Hold.** Out-of-power: same obstacle pose as `error`, then `dead.wav` (~3.4 s, "Insufficient resources, shutting doooooowwwwnnnn..."). **Classic eyes:** failing-display flicker (irregular heights + brief blank pulses, denser near the end of the first line); at ~1.7 s ("shutting down") eyes squeeze nearly shut (~300 ms) while head/hands collapse over **~500 ms**, hold shut briefly (~200 ms), then snap to **X X** while the drawn-out shutdown still plays. If audio fails to start, collapse immediately. Stays with X eyes and pulsing red until another animation is requested. **Cover:** same flicker and squeeze as height changes, then both halves stay black (no X). **Dots:** same flicker and squeeze as circle size, then X marks in 12×12 boxes. **Kaomoji:** Sad face (no X). Does not auto-return to `none`. Requires `dead.wav` on LittleFS. |
| `wakeup` | **One-shot** sleep-inertia wake (~5.5 s + settle), same sequence as boot `loading=sleep_inertia`. **Classic eyes:** open over 2 s (cubic ease) from closed, two blinks at 2.4 s and 3.8 s. Head rises from chin-down with a fading neck wave. Always moves head/neck/hands (API path). After completion, `GET /anim` reports `none`. Does not play welcome audio — use `welcome` for that. |
| `sleep` | **One-shot** nod-off (~2 s). Lids droop toward nearly shut (0–0.7 s) while the head barely moves, then a small resist: lids ease back toward half open as the head starts down (0.7–1.1 s). Lids then shut and the head eases to chin-down (`SLEEP_HEAD_DOWN`, same pose `wakeup` starts from) by 2.0 s. OLED blanks when the lids hit shut (`DISPLAYOFF`). **Classic, cover, dots:** that lid curve. **Kaomoji:** Sleep face during the close window, then blank. Same path as the idle `sleep_timeout`. Holds `sleep` until the head settles; then `GET /anim` reports `none` while the device stays asleep until another non-`sleep`/`none` animation wakes it. After the sleep pose is still for 2 s, PWM is full-off (same limp as `none`). |

#### Kaomoji eyes

When `eyes_style=kaomoji`, the OLED shows looping faces from the expression library (3 s / 30 frames). Mapping:

| Animation / phase | Face |
| --- | --- |
| `none` (idle) | Idle |
| `typing`, `thinking` | Thinking |
| `reading`, `attention`, `listening` | Curious |
| `ring` | Surprise |
| `welcome`, `talking` | Happy |
| `error` | Angry |
| `abort` | Panic |
| `wakeup` | Sleepy |
| `sleep` / sleep-close | Sleep |
| sleep-open | Idle |
| `dead` | Sad |

#### Cover eyes

When `eyes_style=cover`, each eye is a solid white rectangle filling one half of the 128×32 panel (left `x=0`, right `x=64`, width 64). Open eyes are the full 32px tall, so a mask hole of any shape over that half stays lit. Animations keep the classic pose and blink timing, but only height changes: leftover rows are black on the top and/or bottom. A blink grows both margins. A look-up leaves more black on the bottom; a look-down leaves more on the top. Horizontal glance does not move the light. The `dead` X hold paints both halves black.

#### Dots eyes

When `eyes_style=dots`, each eye is a filled circle. At the classic open height the circle’s bounding box is 12×12 px, centered on the classic eye. Diameter scales with rendered height (`12 * height / 14`), so blinks, squints, wide looks, and the `dead` flicker shrink or grow the dots. Height 0 draws nothing (full blink or sleep close). Glance, scan, and look-up/down move the circle with the classic center. The `dead` X hold draws X marks in 12×12 boxes at those centers. Diameter is capped at the 32 px panel height.

Wrong params return **400**:

| `error` | When |
| --- | --- |
| `missing name` | Query param `name` absent |
| `unknown animation` | `name` not `none`, `typing`, `reading`, `thinking`, `talking`, `listening`, `ring`, `welcome`, `attention`, `error`, `abort`, `dead`, `wakeup`, or `sleep` |

### `POST /play`

Streams a WAV to the speaker while one animation runs, then returns to the continuous animation (`typing`, `reading`, `thinking`, `talking`, `listening`) that was running before the request, otherwise to `none`. The body is played as it arrives, so clips of any length need no storage on the robot. The response comes when the clip ends.

| Part | Value |
| --- | --- |
| Path | `/play` runs `talking`; `/play/{name}` runs `talking`, `typing`, `reading`, `thinking`, `listening`, or `none`. Animations with their own sound are rejected so two clips never overlap |
| Body | WAV, 16-bit mono PCM at 22050 Hz (the firmware `SAMPLE_RATE`). `Content-Length` is required. Chunks other than `fmt ` and `data` are skipped |
| `Authorization` | `Bearer <token>` when `access_token` is set |

The animation starts with the first audio sample, so a rejected request never moves the robot. While the clip plays the robot keeps its eyes, LED and sleep timer running, and the clip counts as activity. When the pose is `talking`, the firmware derives speech energy from the streamed PCM and drives reactive head/hand/eye motion; other poses ignore audio for motion.

```bash
curl -X POST http://tiny-engineer.local/play -H 'Content-Type: audio/wav' --data-binary @clip.wav
curl -X POST http://tiny-engineer.local/play/thinking -H 'Content-Type: audio/wav' --data-binary @hmm.wav
```

```json
{ "ok": true, "played_ms": 3000 }
```

The unified CLI wraps this for agents: `tiny-engineer play clip.wav [--name thinking]` ([integration.md](integration.md#1-unified-cli-default)).

Errors arrive before any audio plays:

| Status | `error` | When |
| --- | --- | --- |
| `400` | `name must be talking, typing, reading, thinking, listening or none` | `{name}` is unknown or has its own sound |
| `415` | `expected 16-bit mono PCM at 22050 Hz` | WAV format or sample rate differs |
| `415` | `not a WAV file`, `no audio data` | Body is not a RIFF/WAVE file, or has no `data` chunk |

### `GET /mic`

Mic state. Safe, no hardware side effects. Optional hardware: INMP441 I2S mic on GP6 and a mute button or on/off switch on GP7 ([pinout](hardware/pinout.md)). Without them the routes still answer (`present` stays `false`).

```bash
curl http://tiny-engineer.local/mic
```

```json
{ "ok": true, "muted": false, "streaming": true, "present": true, "level": 0.042, "mute_input": "button" }
```

| Field | Meaning |
| --- | --- |
| `muted` | Mic muted. Button mode: saved in NVS (`mic_mute`), so it survives a reboot. Switch mode: the switch position (open = muted) |
| `streaming` | Audio packets are going out ([Mic stream](#mic-stream-udp)): not muted, Wi-Fi up, `brain_host` set and resolved, last send accepted |
| `present` | Mic data seen (non-constant samples) since boot |
| `level` | Recent RMS of the mic signal after `mic_shift`, `0`–`1` (three decimals); `0` while muted |
| `mute_input` | `button` or `switch` (setting `mute_switch`) |

### `POST /mic`

Mute or unmute (button mode only). The momentary button on GP7 toggles the same state: its own task reads it every 5 ms (30 ms debounce), so a press is never lost to a busy loop, and the mic stream honours it on the very next packet. A `POST /mic` sets the state outright (presses before it are settled; presses after it toggle again). JSON body.

| Field | Type | Range |
| --- | --- | --- |
| `muted` | boolean | Required; saved to NVS |

```bash
curl -X POST "http://tiny-engineer.local/mic" -H "Content-Type: application/json" -d '{"muted":true}'
```

Reply: same JSON as `GET /mic`. Missing or non-boolean `muted` → **400** `invalid muted`. In switch mode (`mute_switch` = `true`) → **409** `mute follows the switch`, and nothing changes.

**Switch mode** (`mute_switch` = `true`, for a maintained 2-pin on/off switch from GP7 to GND): the switch position is the mute state. Closed ("I") = listening, open ("O") = muted, so a broken or unplugged wire fails safe to muted. The same task reads it every 5 ms with a 30 ms debounce and the mic stream follows it on the next packet, even while another request is running. Nothing is written to NVS on a flip. After boot the mic counts as muted until the switch has read closed for 30 ms. Turning switch mode off keeps the mute the switch gave. Leave switch mode off when nothing is on GP7, or the robot stays muted.

While muted the robot shows a mark: a crossed-out microphone in the top-right corner of the OLED eyes (every eye style), and the RGB LED glows **dim amber** wherever the animation would leave it off (`none`, `sleep`, `listening`; also visible while the OLED sleeps).

### `POST /pose`

Smooth free-form move for idle personality and "look at you". Stops the current animation (it becomes `none`, without the `none` park), counts as activity (wakes the eyes and OLED from sleep and restarts the idle clock), and returns once the move has **started**. JSON body.

| Field | Type | Range |
| --- | --- | --- |
| `pose` | array | 5 entries `[head, neck, left, right, body]`, each `-1`–`1` in the saved range (same pose space as animations), or `null` to leave that joint where it is |
| `ms` | integer | `100`–`5000`, time to arrive (eased, cubic in/out); default `600` |

```bash
curl -X POST "http://tiny-engineer.local/pose" -H "Content-Type: application/json" \
  -d '{"pose":[0.3, null, null, null, -0.2], "ms": 600}'
```

```json
{ "ok": true }
```

Targets are clamped to the soft limits ([`GET /ranges`](#get-ranges)). The move still respects `SERVO_MAX_SPEED_DEG_S` (140°/s): when `ms` is too short for the distance, the move stretches until the eased peak speed fits. A later `/anim`, `/pose`, or a bump cancels it. After the joints are still for 2 s, PWM goes full-off as in `none`.

| Status | `error` | When |
| --- | --- | --- |
| `400` | `invalid pose` | `pose` missing, not an array of 5, or an entry that is neither `null` nor a number in `-1`–`1` |
| `400` | `invalid ms` | `ms` not an integer in `100`–`5000` |
| `503` | `busy` | A serial `/play` stream holds the robot |

### `GET /ranges`

Servo limits, servo-rail current and the bump log. Safe, no hardware side effects.

```bash
curl http://tiny-engineer.local/ranges
```

```json
{
  "ok": true,
  "saved_mins": [40, 50, 60, 70, 80],
  "saved_maxs": [140, 130, 120, 110, 100],
  "soft_mins": [40, 50, 60, 70, 80],
  "soft_maxs": [128, 130, 120, 110, 100],
  "hard_mins": [10, 10, 10, 10, 10],
  "hard_maxs": [170, 170, 170, 170, 170],
  "current_ma": 123.4,
  "sensor": true,
  "bump_threshold_ma": 650,
  "bumps": [
    { "t_ms": 812345, "joint": 0, "angle": 130.2, "dir": "max", "current_ma": 980.1, "anim": "typing", "kind": "bump" }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `saved_mins` / `saved_maxs` | NVS calibration `[head, neck, left, right, body]` in degrees; animations and `/pose` map `-1`–`1` onto it |
| `soft_mins` / `soft_maxs` | Runtime limits (RAM only): start equal to saved, narrowed by bumps. Animations, `/pose` and `/test/servo` are clamped to them. Reset to saved at boot, on `POST /ranges`, on `POST /bumps/clear`, and whenever the saved calibration changes |
| `hard_mins` / `hard_maxs` | Compile-time caps (`SERVO_HARD_MIN_DEG` / `SERVO_HARD_MAX_DEG` in [`include/servos.h`](../include/servos.h)), 10°–170° on every joint. `POST /ranges` must stay inside them and `POST /ranges/explore` never drives past them. They do not clamp the saved calibration at runtime (a wider range saved by the setup wizard keeps working as before) |
| `current_ma` | Latest servo-rail current (mA), or `null` without a working INA219 |
| `sensor` | INA219 found at `0x41` at boot **and** still answering; `false` after 5 failed reads in a row (bump reflex off, explore refused) until a read succeeds again |
| `bump_threshold_ma` | Same as the setting |
| `bumps` | Newest first, at most 32 (RAM only): event time (`millis()`), joint, commanded angle when the current first crossed the threshold (degrees), travel direction, peak current of the event window (mA), the animation that was running, and `kind`: `bump` (while moving) or `stall` (high current after the move ended) |

### `POST /ranges`

Save a new servo calibration **outside setup mode** (the brain's range coaching). JSON body; both fields required. Values are degrees; numbers are rounded to whole degrees (NVS stores integers).

| Field | Type | Range |
| --- | --- | --- |
| `servo_mins` | number array | 5 values; each `hard_min` ≤ min < max |
| `servo_maxs` | number array | 5 values; each max ≤ `hard_max`, and max − min ≥ 6° |

```bash
curl -X POST "http://tiny-engineer.local/ranges" -H "Content-Type: application/json" \
  -d '{"servo_mins":[40,50,60,70,80],"servo_maxs":[140,130,120,110,100]}'
```

Reply: same JSON as `GET /ranges`. Soft limits reset to the new saved values; the bump log is kept. The setup wizard's own path (`POST /settings` with `servo_mins` / `servo_maxs` in setup AP) is unchanged.

| Status | `error` | When |
| --- | --- | --- |
| `400` | `servo_mins and servo_maxs go together` | One of them missing |
| `400` | `invalid servo_mins` / `invalid servo_maxs` | Not an array of 5 numbers in `0`–`180` |
| `400` | e.g. `left min 9 below hard min 10`, `body max 171 above hard max 170`, `head min must be below max`, `neck span below 6 deg` | Validation failed (first joint that fails, in `head, neck, left, right, body` order) |
| `400` | `save failed` | NVS write failed |

### `POST /ranges/explore`

Probe how far one joint can really go. **Blocking** (like `/test/*`, at most ~14 s): stops the animation and any `/pose` move, holds the other joints where they are (PWM on; limp servos are switched on and given 400 ms to settle), learns the holding current for at least 300 ms, moves the joint at 60°/s to its saved limit in that direction, then moves it slowly toward the hard cap. The sweep ends at the first of: a bump, the hard cap, **30°** past the saved limit, or 12 s since the start. The joint then backs off 4° from the angle of first contact and returns inside its soft limits. **Saves nothing**; the brain decides what to keep with `POST /ranges`. Needs a working INA219. JSON body.

| Field | Type | Range |
| --- | --- | --- |
| `joint` | integer | `0`–`4` (`head, neck, left, right, body`) |
| `dir` | string | `min` or `max` |
| `speed` | number | `5`–`30` °/s; default `10` |

```bash
curl -X POST "http://tiny-engineer.local/ranges/explore" -H "Content-Type: application/json" \
  -d '{"joint":0,"dir":"max","speed":10}'
```

```json
{ "ok": true, "joint": 0, "dir": "max", "stopped_at": 151.3, "reason": "bump", "safe": 146.1, "peak_ma": 1042.5 }
```

| Field | Meaning |
| --- | --- |
| `stopped_at` | Angle (degrees) where the joint stopped |
| `reason` | `bump` (current over the baseline + threshold for 60 ms, or ≥ 2500 mA at once), `hard_limit` (reached the hard cap), or `max_travel` (stopped 30° past the saved limit, or by the 12 s time limit, before anything was felt) |
| `safe` | Angle 4° back toward the saved mid from first contact (for `bump`) or from `stopped_at` (degrees, inside the hard caps) |
| `peak_ma` | Highest servo-rail current while moving (mA) |

The bump threshold while exploring is `min(bump_threshold_ma, 400)` (400 when the setting is `0`). Explore bumps do not enter the bump log or change the soft limits.

| Status | `error` | When |
| --- | --- | --- |
| `400` | `invalid joint` / `invalid dir` / `invalid speed` | Field missing (except `speed`) or out of range |
| `409` | `no current sensor` | No INA219 at `0x41` at boot |
| `409` | `current sensor not responding` | The INA219 stopped answering (see `sensor` in [`GET /ranges`](#get-ranges)) |
| `409` | `implausible servo current; check INA219 VIN+/VIN- wiring` | Holding current after the settle is below 10 mA (or negative) or above 1500 mA: the shunt is reversed or not in the servo lead. Nothing moved |
| `500` | `current sensor failed during explore` | 5 current reads in a row failed mid-run; the joint returns to where it started |
| `500` | `could not read a steady servo current` | No holding-current baseline within 1 s of holding still (intermittent reads). Nothing moved |
| `503` | `busy` | A serial `/play` stream holds the robot |

### `POST /bumps/clear`

Empty the bump log and reset the soft limits to the saved calibration. No body.

```bash
curl -X POST "http://tiny-engineer.local/bumps/clear"
```

```json
{ "ok": true }
```

### Bump reflex

With an INA219 at `0x41` (servo rail current, [interfaces](hardware/interfaces.md#i2c)), the firmware reads the current every loop tick (at most every 5 ms; ticks run every 10–20 ms while the OLED redraws).

- **Moving** means motion was commanded in the last **250 ms**: a joint slewing toward its target, a target change (a slow `/pose` re-targets every tick), or a `/pose` move in flight. The tail covers servos that keep pushing after the commanded angle arrives.
- **Baseline:** idle current, learned only while nothing moves, kept separately for powered and limp (PWM off) servos. A baseline counts after 10 still samples; until the powered one exists (shortly after boot) only the absolute trip guards moves. Samples above `baseline + bump_threshold_ma / 2` never feed it (a load between half and the full threshold is learned only after 2 s of stillness), and detection pauses for 400 ms after released outputs switch back on.
- **Bump:** current above `baseline + bump_threshold_ma` for **≥ 60 ms** while moving, or ≥ 2500 mA at once.
- **Stall:** current above `baseline + bump_threshold_ma` for **≥ 300 ms** with powered servos and no motion (a joint pressing into something after its move ended).

On a bump or stall the firmware:

1. Switches the animation to `none` (without the `none` park) and stops every joint where it is.
2. Blames joints. **Bump:** every joint that moved in the last 250 ms, in the direction it was travelling. **Stall:** only when exactly one joint's own motion ended within the last 650 ms, in the direction of that move (the reflex's own stops and back-offs never count as motion anyone can be blamed for, and never change a joint's direction). Anything less certain switches all servo outputs off instead. For each blamed joint, it takes the commanded angle from when the current **first** crossed the threshold (near first contact, not where the joint ran to), moves 4° back from it against the travel direction at 25°/s (on an outward move it stops at the saved mid when closer than that), and sets that side's soft limit to that angle ∓ 2°, never past the saved mid and never wider than before.
3. Logs one entry per blamed joint ([`GET /ranges`](#get-ranges), `kind` `bump` or `stall`).
4. When no joint can be blamed with certainty, it switches every servo output off instead. While `POST /servos/park` holds the outputs on, such an event is logged once and ignored.

Further events are ignored for 500 ms. The reflex never widens anything; only `POST /ranges` (validated) can. It is off in setup AP mode, with `bump_threshold_ma` = `0`, without the sensor, and while the sensor stops answering (5 failed reads in a row; it re-learns its baselines when reads come back).

### Mic stream (UDP)

With an INMP441 fitted and `brain_host` set, the robot streams the mic to `brain_host:brain_port` over UDP, only while Wi-Fi is connected and the mic is not muted. Wire format (contract: [`chip/PROTOCOL.md`](../chip/PROTOCOL.md) §3): a 16-byte header, then 16-bit signed little-endian **mono** PCM at **22050 Hz**, 256 samples per packet (~86 packets/s).

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | magic `CHP1` |
| 4 | 4 | `seq` uint32 LE, +1 per packet (heartbeats included), wraps |
| 8 | 4 | `t_ms` uint32 LE, robot `millis()` of the first sample |
| 12 | 2 | `flags` uint16 LE: bit0 `muted`, bit1 `speaker` (any speaker audio playing, or ended < 300 ms ago), bit2 `moving` (any servo moving), bit3 `asleep` |
| 14 | 2 | `n` uint16 LE, sample count (256; `0` for a heartbeat) |

While muted: no audio, one heartbeat (`n = 0`, `muted` set) per second. The brain must discard audio with `speaker` set. A hostname in `brain_host` is resolved (DNS / mDNS, IPv4 only) by a separate task, so a slow lookup never stalls the mic reads or the mute button; it is retried every 5 s after a failure and refreshed every 60 s. An IPv4 literal skips DNS. Samples are `saturate(slot >> mic_shift)` from the left 32-bit I2S slot (the INMP441 sends 24-bit data, MSB aligned).

## Errors

| Status | Body | When |
| --- | --- | --- |
| `400` | `{"ok":false,"error":"..."}` | Bad body or fields on `/settings`, `/settings/wifi`, `/test/servo`, `/setup/*`, `/mic`, `/pose`, `/ranges` or `/ranges/explore`, or bad `/anim` or `/play` params (see tables above) |
| `401` | `{"ok":false,"error":"unauthorized"}` | Access token configured and `Authorization: Bearer` missing or wrong |
| `404` | `{"ok":false,"error":"not found"}` | Unknown path |
| `409` | `{"ok":false,"error":"..."}` | `POST /ranges/explore` without a working INA219, or with an implausible holding current; `POST /mic` in switch mode (`mute follows the switch`) |
| `415` | `{"ok":false,"error":"..."}` | `/play` body is not a supported WAV |
| `500` | `{"ok":false,"error":"current sensor failed during explore"}` | `POST /ranges/explore` lost the current sensor mid-run |
| `503` | `{"ok":false,"error":"busy"}` | `POST /pose` or `POST /ranges/explore` while a serial `/play` stream holds the robot |
| `405` | `{"ok":false,"error":"method not allowed"}` | Wrong method on any known path, web panel pages included |

Test routes are **POST**. GET/prefetch would move hardware. `/anim` allows **GET** (read) and **POST** (set). `/auth` is **GET** only and always public.

## Behaviour

Test handlers **block** until the test finishes. The client waits. After each test, OLED returns to `ROBOT READY` plus IP (or `WIFI FAIL`).

`/anim` responses return immediately; typing motion runs in the main loop via non-blocking servo updates. Animation switches may defer up to 1s so the active animation holds its minimum duration; only the latest pending request is applied. Continuous animations (`typing`, `reading`, `thinking`, `talking`, `listening`) that stay active longer than `continuous_timeout` minutes switch to `attention`, which holds ~1 minute then finishes to `none`. Re-`POST`ing the same continuous animation (e.g. `typing` while already `typing`) does not restart motion, but resets the continuous-timeout clock.

The onboard RGB LED follows the active animation (see [RGB LED](#rgb-led)).

`/play` holds the connection until the clip ends. `POST /ranges/explore` blocks like the test routes (at most ~14 s).

One request at a time — the Arduino `WebServer` is single-threaded. While `/play` streams, other requests wait.

## RGB LED

Onboard WS2812 on **GPIO10** (`RGB_LED_PIN`). Byte order is the saved `rgb_order` setting (default **GRB**). Animation-driven colors are handled in [`src/hardware/rgb.cpp`](../src/hardware/rgb.cpp) and switch when `POST /anim` applies a new state (same 1s minimum hold as servos/eyes).

| Animation | LED color |
| --- | --- |
| `typing`, `reading`, `thinking`, `talking`, `welcome`, `ring`, `wakeup` | White (full intensity) |
| `attention`, `error`, `dead` | Red pulse: 500 ms 10%→100%, 500 ms hold 100%, 500 ms 100%→10%, repeat |
| `abort` | Red (full intensity, solid) |
| `none`, `sleep`, `listening` | Off — **dim amber** instead while the mic is muted |

Transitions take **1 s** with smooth fade in/out (pulse modes start immediately, no enter fade):

- **Off ↔ color** — single 1 s fade (e.g. idle → typing fades in white; ring → `none` fades out).
- **White ↔ solid red** — fade out to black (500 ms), then fade in to the new color (500 ms).
- **Leaving pulse** — 1 s fade from the current pulse brightness to the new target.

Switching between animations that share the same color (e.g. `typing` → `reading`) does not restart a fade.

Boot uses dim green `(0, 32, 0)` as a status indicator during init. After `ROBOT READY`, the LED fades to white if `welcome` runs (Wi-Fi OK) or off if idle. Fatal PCA9685 / I2S errors hang and blink the LED red (1 = PCA9685 missing, 2 = I2S failed) — not animation-driven. See [boot-failure blink codes](hardware/testing.md#boot-failure-blink-codes).

`POST /test/led` runs a hardware colour cycle and then restores the current animation LED state.
