# Chip brain

Chip is a Tiny Engineer that listens. The robot streams its mic to this
program on your always-on Windows PC. The brain hears "Hey Chip", answers with
Chip's own local AI (Ollama) or hands harder questions to Claude, and speaks the
answer through the robot. It also watches your Claude Code
sessions on the VMs and drives the robot's animations for the one in focus.

```
 VMs (Claude Code + chip-hook.js) ──HTTP :47101──►  BRAIN (this PC)  ──HTTP──► ROBOT
                                                        ▲                       │
                                                        └──── UDP :47100 mic ───┘
```

Contract with the firmware: [`../PROTOCOL.md`](../PROTOCOL.md).

> **Run it on a real PC on the same Wi-Fi as the robot.** The robot must be able
> to reach this PC (it sends the mic stream to it). A VM behind NAT can't get the
> UDP stream. If you must use a VM, give it bridged networking.

## Setup (home PC, about 15 minutes)

1. **Install Python 3.11 or newer** from python.org. Tick **"Add python.exe to PATH"**.
2. **Get the code** on the PC (this repo, branch `chip`). Open a terminal in `chip\brain`.
3. **Install the parts:**
   ```
   py -m pip install -r requirements.txt
   ```
4. **Install Chip's local AI:** get Ollama from ollama.com/download, then in a terminal:
   ```
   ollama pull qwen3:4b
   ```
   Skip this and Claude answers everything. To use a different model, see [Chip's local AI](#chips-local-ai).
5. **Set your Anthropic API key** (from console.anthropic.com), so Chip can ask Claude. Then close the terminal and open a new one.
   ```
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```
   API usage is billed separately from a Claude Pro/Max subscription.
6. **Make your config:**
   ```
   copy chip.example.toml chip.toml
   notepad chip.toml
   ```
   Check `[robot] url` (the IP on the robot's screen). If the robot has an access token, put it in `token`.
7. **Open the firewall.** In PowerShell **as Administrator**:
   ```powershell
   New-NetFirewallRule -DisplayName "Chip mic (UDP 47100)" -Direction Inbound -Protocol UDP -LocalPort 47100 -RemoteAddress 192.168.86.223 -Action Allow
   New-NetFirewallRule -DisplayName "Chip hooks (TCP 47101, Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 47101 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
   If Windows pops up a firewall prompt for Python on first run, allow **Private networks**.
8. **Tell the robot where the brain is:**
   ```
   py -m chipbrain point-robot
   ```
   It finds this PC's Wi-Fi IP and does `POST /settings {"brain_host": "<this PC>", "brain_port": 47100}` on the robot.
   Manual version: `curl -X POST http://192.168.86.223/settings -H "Content-Type: application/json" -H "Authorization: Bearer TOKEN" -d "{\"brain_host\":\"192.168.86.50\"}"`
9. **Check, then start:**
   ```
   py -m chipbrain check
   py -m chipbrain
   ```
   Or double-click `start-chip.bat`. The first start downloads the Whisper model (about 150 MB).
   You should see `listening for the mic on UDP 47100`. Open http://localhost:47101/status: `mic.streaming` should be `true`.
10. **On each VM** (needs Node.js), from the repo's `chip\hooks` folder. Use the PC's Tailscale name or 100.x IP:
   ```
   node install.js --url http://HOME-PC:47101
   ```
   This adds Chip's hooks to `%USERPROFILE%\.claude\settings.json` (backup made first) and **removes the old
   tiny-engineer hooks**, so only the brain drives the robot. Restart Claude Code sessions afterwards.
   Preview with `--dry-run`. Undo with `node install.js --uninstall`.
   If a project has its own tiny-engineer hooks: `node install.js --clean-project C:\path\to\project`.
11. **Say "Hey Chip, what's going on?"**

Start with Windows: press Win+R, type `shell:startup`, and put a shortcut to `start-chip.bat` there.

## Things to say

| Say | Chip |
| --- | --- |
| "Hey Chip, what's JobSearch doing?" | Summarises the session |
| "Chip, watch Flipopolis." / "Chip, go back to auto." | Changes focus |
| "Chip, what's running?" | Lists sessions |
| "Chip, find your ranges." | Slowly feels each joint's limits and saves safer ones |
| "Chip, tighten your head max by 5 degrees." | Adjusts one limit |
| "Chip, mute yourself." | Mutes the mic (unmute with the button on the robot) |
| "Chip, remember I like one-sentence updates." | Saves it; every answer follows it from now on |
| "Chip, forget the standup thing." | Removes what it learned about that |
| "Chip, ask Claude why the build failed." | Says "Let me ask Claude" and Claude answers |

After Chip answers you have 8 seconds to reply without saying "Chip".

## How it behaves

* **Who answers:** Chip's local AI answers first. When a question needs real reasoning
  (why something failed, what to do next in your code), it says "Let me ask Claude" and
  Claude answers. If Ollama isn't running, or the local AI takes longer than 3 seconds to
  start answering, Claude answers instead. `GET /status` shows who answered last (`answered_by`).
* **Teaching:** what you ask Chip to remember goes in `chip_memory.json` next to `chip.toml`.
  Both the local AI and Claude read it on every turn. Edit or delete the file any time.
* **Wake word:** Whisper transcribes the first 1.2 s of what you say. If it starts with
  "Chip", the robot turns to you (`listening`) before you finish. Sound-alikes like
  "ship" only count after "hey", so "ship it" never wakes it. Lists are in `[wake]`.
* **Focus:** auto mode follows the most recently active session, but only switches when the
  current one has been idle 2 minutes or has ended, and says so ("Switching to JobSearch on VM 2").
  Only the focused session moves the robot (same mapping as `tiny-engineer hook claude-code`).
* **Heads-up:** when another session finishes or needs permission, Chip says one line
  ("Heads up, Flipopolis on VM 1 is waiting for you"). At most once per 5 min per session.
  Turn off with `[focus] heads_up = false`.
* **Range coach:** two bumps at the same limit within 10 minutes tighten that limit a little.
  Ranges never cross the middle, never go below a 12° span, never pass the hard caps,
  and only loosen up to what "find your ranges" measured.
* **Idle:** small glances every 20–90 s when nothing is happening, a stretch after a long task,
  a glance at you after a loud bang. Calmer after 23:00.
* **One robot request at a time:** speech first, then other calls, then the latest animation.

## Chip's local AI

Settings live in `[local]` in `chip.toml`. The model must support tools (ollama.com/search?c=tools).

| Your PC | Try |
| --- | --- |
| NVIDIA card with 8 GB or more | `qwen3:8b` |
| Smaller card, or no card | `qwen3:4b` (default) |
| Replies feel slow | a smaller model, or set `enabled = false` to let Claude answer everything |

* `ollama pull <model>` first, then set `model = "<model>"` and restart Chip.
* `ollama ps` shows whether the model is loaded and whether it runs on the GPU or CPU.
* `first_token_timeout_s` is how long Chip waits for the local AI before asking Claude instead.

## Voice options

* **Default:** Windows built-in voices (SAPI). Pick one with `[tts] sapi_voice = "Zira"`.
* **Nicer voice (Piper):** `py -m pip install piper-tts`, download a voice (both files) such as
  `en_US-lessac-medium.onnx` and `en_US-lessac-medium.onnx.json` from huggingface.co/rhasspy/piper-voices
  into `chip\brain\voices\`, then set `piper_model = "voices/en_US-lessac-medium.onnx"`.
  If `piper-tts` won't install, get `piper.exe` from the Piper GitHub releases and set `piper_exe`.
* Test any voice: `py -m chipbrain say "Hello Joe"`.

## Brain HTTP (port 47101)

| Route | Use |
| --- | --- |
| `POST /events` | hook intake from `chip-hook.js` |
| `GET /status` | focus, sessions, mic, last transcript, robot |
| `POST /say` | `{"text": "..."}` speak a line |
| `POST /focus` | `{"session": "Flipopolis"}` or `{"session": null}` for auto |

If `[brain] token` is set, send it as `X-Chip-Token` (install.js `--token` does this for hooks).

## Troubleshooting

| Problem | Check |
| --- | --- |
| `mic.streaming` is false | Step 7 firewall rule, step 8 `brain_host`, robot on Wi-Fi, mic not muted |
| Chip hears but never answers | Ollama running (`ollama ps`), or `ANTHROPIC_API_KEY` set in a **new** terminal; `py -m chipbrain check` |
| "the model ... isn't downloaded yet" in the log | `ollama pull qwen3:4b` (or the model in `[local] model`) |
| Claude answers everything | Ollama not running, or too slow: check `ollama ps`, try a smaller model |
| Wakes by accident | Remove words from `[wake] words_after_greeting` |
| Misses "Chip" | Try `[stt] model = "small.en"`; speak after a short pause |
| VMs not showing in `/status` | `node install.js --url ...` on that VM, Tailscale up, TCP 47101 rule |
| Looks down instead of at you | Set `[idle] look_at_joe_head = -0.25` |

## Tests

```
py -m pip install pytest
py -m pytest tests            (in chip\brain)
node --test                   (in chip\hooks)
```

The tests use a fake robot that checks the PROTOCOL.md contract (WAV format, one request at a time)
and fake speech/Claude backends, so they run without a robot, a mic or an API key.
