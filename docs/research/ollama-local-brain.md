# Research: a local Ollama "brain" for Chip, with Claude on call

Question: can a local model served by Ollama run Chip's voice conversation loop
well enough to feel natural, with Claude (Anthropic API) brought in only when
needed? Context: the brain in `chip/brain/` already streams Claude replies with
tools and speaks them sentence by sentence (`claude.py`, `speech.py`), using
faster-whisper for STT and Piper or Windows SAPI for TTS (`stt.py`, `tts.py`).
Joe's home PC runs Windows; its hardware is unknown and it may be a VM.

Researched 2026-11-05 against primary sources (Ollama docs and source at commit
`6362e0a`, model cards, Home Assistant docs and source, Anthropic docs,
faster-whisper, Unsloth). Benchmarks come only from the people who ran them,
with their hardware. Numbers marked **estimate** are my own arithmetic from
those sources, not measurements.

## Bottom line

- **Yes, it can work, but the PC's hardware decides how well.** With an NVIDIA
  GPU (8–12 GB), a 4–9B model generates about 60–130 tokens/s on a
  7B-class model [25], and Home Assistant users report about 1–3 s per
  voice reply from local LLM setups on 16–24 GB GPUs [36]. On CPU only, use
  a 2–4B model and expect a few seconds before Chip starts talking. In a
  VM with no GPU, an 8B model ran at 2.8–6.8 tokens/s on 4–8 vCPU cloud
  VMs [29]. That is too slow for natural chat; a 2–4B model is the
  ceiling there.
- **The code change is small.** Ollama serves the Anthropic Messages API,
  with streaming and tools [12]. The brain's existing `AnthropicBackend` can
  point at `http://<pc>:11434` and keep its streaming and tool loop. The
  limits: no `tool_choice` and no `cache_control` prompt caching, and
  context length and keep-alive must be set on the Ollama side.
- **The biggest latency trap is prompt processing on CPU, not generation.**
  Laptop and desktop CPUs process 7–8B prompts at only about 13–120
  tokens/s [27][28], so a 1,000-token system prompt can take seconds to
  tens of seconds unless it is reused. Keep the system prompt short and
  unchanged between turns, put changing session notes at the end, and
  keep the model loaded (`keep_alive: -1`) [5].
- **Small models are the weak spot for tool calling.** Home Assistant's own
  docs warn that small models make more mistakes and may not hold a
  conversation once tools are on [32]. Give the local model only a handful
  of simple tools (chat, remember, mute, focus, `ask_claude`), check its
  tool arguments in code, and escalate when it fails.
- **Escalate by rules and failures, not by the model's own confidence.** Keep
  the brain's existing keyword router (`choose_model`). Add an `ask_claude`
  tool, and escalate automatically on bad tool calls, timeouts, or "ask
  Claude". Research finds model self-assessment poorly calibrated on new
  tasks [41].
- **"Teach" it with a memory file and a short prompt first.** Fine-tuning
  comes later, only if logs show a consistent gap. It needs an NVIDIA GPU
  and at least 100 (ideally 1,000+) examples [45]. Current Ollama no longer
  accepts LoRA adapters, so a tuned model must be merged and exported to
  GGUF [16].
- **Claude should keep the work that matters:** questions about Claude Code
  sessions (reasoning over session notes), the joint-range tools (they move
  hardware), anything multi-step, and whatever the local model fails at.
  Claude Haiku 5.5 is Anthropic's "fastest" tier, for "latency-sensitive
  tasks such as classification, extraction, and routing" [42], so it fits
  as a cheap middle step.
- **First step:** find out what the PC actually has. Run `nvidia-smi` on
  Windows; after loading a model, `ollama ps` shows `100% GPU` or
  `100% CPU` [5]. That one answer picks the model size and sets the
  latency.

## 1. Ollama's current chat API

**Native chat endpoint.** `POST /api/chat` streams by default and takes
`model`, `messages`, `tools`, `think`, `format`, `options`, `stream` and
`keep_alive` [1]. The final chunk reports `load_duration`,
`prompt_eval_count`, `prompt_eval_cached_count` ("number of prompt tokens read
from the cache"), `prompt_eval_duration`, `eval_count` and `eval_duration`, so
the brain can log time-to-first-token and cache hits per turn [1].

**Tool calling.** Tools use the OpenAI-style `{"type":"function","function":{...}}`
schema. The model returns `message.tool_calls`, and results go back as
`{"role":"tool","tool_name":...,"content":...}`. Parallel calls and multi-turn
agent loops are documented [2]. The Ollama docs advise that "it also might
help to tell the model that it is in a loop and can make multiple tool calls"
[2]. Models with tool support are tagged on ollama.com [1][19].

**Streaming with tools.** Supported since May 2025. Ollama added an
incremental parser that reads each model's template to find its tool-call
prefix, so normal text streams while tool calls are pulled out. Before that,
Ollama "had to wait for the complete generation before seeing any streamed
token" [17]. The docs say to collect every chunk's `thinking`, `content` and
`tool_calls` and send them back together on the next request [2][3]. The same
blog post notes, "anecdotally" and without a benchmark, that a 32k+ context
window improves tool calling, at the cost of more memory [17].

**Structured outputs.** `format` takes `"json"` or a JSON schema, and the docs
suggest also putting the schema in the prompt [4]. Ollama's cloud doesn't
support structured outputs [4]. That doesn't matter for local use.

**Thinking.** `think` takes a boolean or `"low"|"medium"|"high"|"max"` [1].
This matters for latency: Qwen 3.5 "will think by default" [22], so a voice
loop should send `think: false`. Home Assistant also defaults `think` to off
[32][33].

**Keep-alive and preloading.** Models stay loaded for 5 minutes by default.
`keep_alive` accepts a duration, a number of seconds, a negative value (stay
loaded) or `0` (unload now). An empty request with `keep_alive: -1` preloads a
model, and the server-wide default is `OLLAMA_KEEP_ALIVE`. The request value
overrides the env var [5]. Home Assistant's integration defaults to `-1`
[32][33].

**Context length.** The docs disagree with each other. The Modelfile table says
`num_ctx` defaults to 2048 [10], the FAQ says 4096 [5], and the context-length
page says the default depends on VRAM: "< 24 GiB VRAM: 4k context" [9]. Set it
explicitly with `options.num_ctx`, `PARAMETER num_ctx` or
`OLLAMA_CONTEXT_LENGTH`, and check the `CONTEXT` column in `ollama ps` [9].
Running parallel requests multiplies memory: "Required RAM will scale by
`OLLAMA_NUM_PARALLEL` * `OLLAMA_CONTEXT_LENGTH`" [5].

**Anthropic-compatible endpoint (most relevant to Chip).** Ollama serves
`/v1/messages`. Point the Anthropic SDK at it with
`base_url='http://localhost:11434'` and any API key [12]. Supported: streaming
(`content_block_delta` including `input_json_delta`), system prompts, `tools`,
`tool_use` and `tool_result` blocks, `stop_reason: tool_use`, and `thinking`
[12]. Not supported: `tool_choice`, `cache_control` prompt caching,
`count_tokens`, and server-sent `error` events (errors come back as HTTP
status codes instead) [12]. So `AnthropicBackend` in
`chip/brain/chipbrain/claude.py` would work against Ollama by changing the
client's `base_url` and model name. `keep_alive` and `num_ctx` aren't fields on
that endpoint, so set them with `OLLAMA_KEEP_ALIVE` and
`OLLAMA_CONTEXT_LENGTH`, or bake `num_ctx` into a Modelfile [5][10]. That last
point is my reading of the field list, not a documented statement.

**Windows.** Ollama "runs as a native Windows application, including NVIDIA and
AMD Radeon GPU support". It needs Windows 10 22H2+ and NVIDIA driver 551.61+
on Windows [6]. The hardware page lists NVIDIA compute capability 5.0+ and
driver 550+ (570+ for compute 5.0–6.2) [7]. Vulkan is on by default for other
GPUs [7]. The API binds to `127.0.0.1:11434`; set `OLLAMA_HOST` to expose it
on the network [5]. A standalone zip exists for running as a service with
`ollama serve` [6].

**Inside a VM without GPU passthrough.** Ollama's docs don't cover VMs. What
they do say: with no usable GPU, Ollama runs on CPU, and its CPU libraries
rank "`cpu_avx2` will perform the best, followed by `cpu_avx` an[d] the slowest
but most compatible is `cpu`" [8]. Whether a VM sees AVX2 depends on the
hypervisor. In Hyper-V, processor compatibility mode "hides newer processor
instructions sets", but it is off by default [31]. GPU partitioning into VMs
is documented for Windows Server 2025 hosts with a short list of datacenter
GPUs [30]. I found no Microsoft source saying a Windows 10/11 Hyper-V host can
pass a consumer GPU to a VM, so assume a VM on Joe's PC is CPU-only unless
`ollama ps` says otherwise.

**New Ollama audio features (Oct 2026, immature).** Ollama now serves
`/v1/audio/transcriptions` for audio-capable models (current models take 30 s
at a time) and `/v1/audio/speech` (v0.40.8+), with a 31M-parameter "Phonon" TTS
model that streams 24 kHz audio [13][14]. Gemma 4 E2B/E4B accept audio input,
up to 30 s [21][23]. These could one day replace faster-whisper and Piper with
a single server. But Phonon support was added in Ollama's MLX runner in
October 2026 (commit history of [14]), and I did not verify that it runs on a
Windows CPU, so treat it as an experiment, not the plan.

## 2. Small and mid-size models with tool calling

Sizes are Ollama download sizes from the tags pages. The real memory footprint
is that plus the KV cache for the context (Ollama can quantise that cache with
`OLLAMA_KV_CACHE_TYPE=q8_0`, roughly halving it [5]). Tool benchmarks are
vendor-reported, and different vendors' "tau2" numbers aren't comparable with
each other.

| Family (Ollama tag) | Sizes with tools | Download size (Q4_K_M unless noted) | Tool-use evidence |
| --- | --- | --- | --- |
| Qwen 3.5 (`qwen3.5`) | 0.8b, 2b, 4b, 9b (+27b, 35b-a3b MoE) [18][20] | 2b 1.9 GB, 4b 3.3 GB (q8_0 5.2 GB), 9b 6.6 GB (q8_0 10 GB) [20] | Qwen card: BFCL-V4 9B 66.1 / 4B 50.3; TAU2 9B 79.1 / 4B 79.9 [22]. Thinks by default [22]. Sizes include a vision encoder. |
| Gemma 4 (`gemma4`) | e2b, e4b, 12b (+26b-a4b MoE, 31b) [18][21] | e2b 4.6 GB, e4b 6.6 GB, 12b 8.0 GB; QAT builds 4.3 / 6.1 / 7.2 GB [21] | "Native function-calling support"; Tau2 E2B 24.5%, E4B 42.2%, 12B 69.0% [21][23]. E-models also take audio. |
| Llama 3.2 / 3.1 (`llama3.2`, `llama3.1`) | 1b, 3b; 8b [18] | 3b 2.0 GB, 8b 4.9 GB (as reported by [29]) | Meta card: BFCL V2 1B 25.7, 3B 67.0, 8B 67.1 [24]. Released 2024, older but well understood. |
| Qwen 3 (`qwen3`) | 0.6b–14b, 30b MoE [18] | not fetched (tags page blocked) | The model Ollama's own tool-calling docs use in examples [2][17]. |
| Mistral NeMo (`mistral-nemo`) | 12b [18] | not fetched | Tagged tools [18]; no fresh evidence gathered. |

Notes:

- **1–2B models are a poor fit for tool calling.** Llama 3.2 1B scores 25.7 on
  BFCL V2 against 67.0 for 3B [24]. Gemma 4 E2B scores 24.5% on Tau2 against
  42.2% for E4B [23]. They are fine for chit-chat with no tools.
- **3–4B models are the realistic floor** for "chat plus a few simple tools".
  **7–9B models are clearly better** at tools (Qwen 3.5: BFCL 50.3 for 4B vs
  66.1 for 9B [22]).
- **Quantisation matters for tools (one first-hand report).** A Home Assistant
  builder found that Ollama's default `:4b` tags, at Q4_K, called tools
  unreliably, and higher-precision GGUFs fixed it [36]. That is a single
  report, not a benchmark, but it's cheap to test (for example `qwen3.5:4b-q8_0`,
  5.2 GB [20]).
- **MoE models are a CPU option if the PC has 32 GB+ RAM.** On a Ryzen 9
  9950X3D, CPU only, gpt-oss-20b (an MoE) generated 27.2 tokens/s against
  8.5 for dense Qwen3-8B at Q8_0 [27]. Qwen 3.5 35b-a3b (22 GB) and Gemma 4
  26b-a4b (16–19 GB) are the same kind of model [20][21]. Their prompt
  processing was still only about 140 tokens/s [27].
- **VRAM budget on an 8 GB card:** the brain also runs faster-whisper. On GPU,
  large-v2 at int8 used 2.9 GB in faster-whisper's benchmark [37]; base.en,
  the brain's default, is much smaller. A 4B model at Q4 (3.3 GB) plus
  Whisper fits in 8 GB. A 9B Q4 model (6.6 GB) plus Whisper is tight on 8 GB
  and comfortable on 12 GB (**estimate**). If the model spills, `ollama ps`
  shows a CPU/GPU split, and it slows down sharply [5][9].

## 3. Realistic speed

What a voice loop cares about: **time to first spoken sentence** ≈ end-of-speech
detection (the brain waits `end_silence_ms = 600`) + STT + LLM prompt
processing of the *uncached* tokens + generating the first sentence (~15–25
tokens) + TTS of that sentence + upload to the robot.

### (a) NVIDIA GPU, 8–16 GB

llama.cpp's CUDA benchmark thread (Llama 2 7B, Q4_0, 3.56 GiB; `llama-bench`;
each row from the person who ran it) [25]:

| GPU | Prompt (pp512) t/s | Generation (tg128) t/s |
| --- | --- | --- |
| RTX 2060 SUPER 8 GB | 1,420–1,564 | 60–61 |
| RTX 4060 Ti 8 GB | 3,395–3,803 | 64 |
| RTX 3060 12 GB | 2,138–2,408 | 76–77 |
| RTX 2070 SUPER 8 GB | 2,088–2,293 | 88 |
| RTX 5060 Ti 16 GB | 3,737–4,196 | 91–93 |
| RTX 5070 12 GB | 5,185–5,783 | 128 |

LocalScore (Mozilla Builders; Q4_K_M; TTFT averaged over a test mix with
prompts of 16–4,096 tokens) [26]: RTX 3070 Ti 8 GB does Llama 3.1 8B at 83
tokens/s with 520 ms TTFT; RTX 3080 Ti 12 GB does 8B at 91 tokens/s / 365 ms
and Qwen2.5 14B at 46 tokens/s / 732 ms. Llama 3.2 1B runs at 189–313 tokens/s
on 8–12 GB cards [26].

End to end, from a first-hand Home Assistant build (llama.cpp, various
4B–35B-MoE models, "response time after prompt caching"): RTX 3090 / RX
7900XTX 24 GB 1–2 s; RTX 5060Ti 16 GB 1.5–3 s; RX 9060XT 16 GB 1.5–4 s; RTX
3050 8 GB about 3 s with a 4B model for "basic functionality" [36].

**Estimate for Chip on an 8–12 GB GPU:** prompt processing is under 0.1 s, and
the first sentence takes 0.2–0.4 s at 60–90 tokens/s, so the LLM adds roughly
0.3–0.7 s on top of STT and TTS. A first spoken word about 1.5–2.5 s after Joe
stops talking is plausible.

### (b) Modern CPU only

- Ryzen 9 9950X3D, 2×16 GB DDR5-6000, llama.cpp b6401, CPU (Phoronix /
  OpenBenchmarking) [27]: Qwen3-8B Q8_0 at 8.5 tokens/s generation and 110–121
  tokens/s prompt; Mistral-7B Q8_0 at 9.7 / 114–120; granite-3.0-3b-a800m (MoE)
  at 68 / 413–466; gpt-oss-20b at 27 / 141–146.
- Laptops, llama 7B Q4_0, first-hand blog [28]: Ryzen AI 7 350 (32 GB) at
  10.1–10.5 tokens/s generation and 31–36 prompt; i7-8550U at 5.6–6.4 / 13–16.
  Pre-2014 CPUs manage about 2 tokens/s.
- LocalScore CPU rows, Llama 3.2 1B Q4_K_M [26]: Ryzen AI 7 PRO 450 at 30.4
  tokens/s with 5.2 s average TTFT; i5-9600KF at 31.9 tokens/s with 11.2 s
  TTFT. These long TTFTs come from the up-to-4,096-token test prompts, which is
  exactly the prompt-processing cost a voice loop must avoid.

**Estimate for Chip on a modern desktop CPU:** a 3–4B model at Q4 should
generate about 15–30 tokens/s, so the first sentence takes about 1 s. That
holds only if the system prompt and tools come from Ollama's cache
(`prompt_eval_cached_count` [1]) and each turn adds only about 50–150 new
tokens. On a cache miss, a 1,000-token prompt at roughly 100–400 tokens/s adds
2.5–10 s.

### (c) Windows VM without GPU passthrough

I found no first-hand benchmark of Ollama in a Windows Hyper-V guest. The
closest first-hand data is CPU-only Linux cloud VMs (DigitalOcean, Ollama
0.32.9, timed with `eval_count/eval_duration`) [29]:

| VM | llama3.1:8b (Q4_K_M) | llama3.2:3b |
| --- | --- | --- |
| 4 shared vCPU, 8 GB | 2.8 tokens/s | 5–7 tokens/s |
| 8 shared vCPU, 16 GB | 5.4 tokens/s | not stated |
| 8 dedicated vCPU, 16 GB | 6.8 tokens/s | 12–15 tokens/s |

**Estimate:** a Windows VM gets at most its share of the host's cores and
memory bandwidth, so expect numbers in this range. It can be worse if the
hypervisor hides AVX2 [8][31]. An 8B model at 3–7 tokens/s takes 3–8 s to say
its first sentence, which doesn't feel natural. A 1–3B model with no or few
tools is the most a GPU-less VM can do conversationally. If Joe's "home PC" is
a VM on a bigger host, running Ollama on the host (or another machine with a
GPU) and pointing the brain at it over the LAN is the better design. Ollama
just needs `OLLAMA_HOST` set to listen beyond localhost [5].

## 4. How others build local voice assistants

**Home Assistant Assist (the reference design).** Pipeline: wake word → STT →
conversation agent → TTS. Local STT and TTS run as Wyoming services [34].
Their own numbers: Whisper takes "around 8 seconds" on a Raspberry Pi 4 and is
"done in under a second" on an Intel NUC. Piper on a Pi with medium voices
"can generate 1.6s of voice in a second" [34]. The closed-vocabulary
"Speech-to-Phrase" STT is "under one second" even on a Pi 4 [34].

**Home Assistant's Ollama integration** [32][33]:

- Options: model, instructions (templated prompt), AI tools, context window
  (default 8k), max history messages (default 20), keep-alive (default -1),
  think (default off).
- In code, it calls `client.chat(..., tools=tools, stream=True,
  keep_alive=..., think=...)` and loops up to `MAX_TOOL_ITERATIONS = 10` [33].
- It repairs small models' tool arguments in code: `_fix_invalid_arguments`
  ("Small models (for example llama3.1 8B) may produce invalid argument values
  which we attempt to repair here"), and drops empty arguments "that will fail
  intent parsing" [33]. Chip's brain should do the same.
- The docs say: "Smaller models are more likely to make mistakes than larger
  models", recommend "exposing fewer than 25 entities", and note that "Smaller
  models may not reliably maintain a conversation when a set of tools is
  selected". Their workaround is two agents on the same model, one for chat
  without tools and one for control [32].

**"Prefer handling commands locally."** Home Assistant tries its built-in
deterministic intent matcher first and only sends unmatched requests to the
LLM. The docs note this is faster but drops the persona [35]. That is the same
"router first" idea Chip's brain already has.

**First-hand builder report** (Nicolas Mowen, Home Assistant community, Oct
2025, since updated) [36]:

- Moved from Ollama to llama.cpp.
- Uses models such as Qwen3 4B Instruct 2507 Q6, Gemma 4 26B-A4B QAT and
  Qwen3.5 35B-A3B, with thinking off, and Parakeet, Qwen3-ASR or Whisper for
  STT.
- Pitfalls he hit, with fixes:
  - Tools not called (the LLM claimed it didn't know the location): fixed by
    a prompt section per tool telling it to always call that tool.
  - Rambling answers: fixed by an example of the wanted output.
  - Emoji reaching TTS: fixed by telling the prompt to omit them.
  - Low-bit quants hurting tool calls: fixed with higher-precision models.
  - Stuttering speech: caused by Wi-Fi; fixed by Piper streaming and a
    dedicated IoT network.

**Pitfalls that apply to Chip (from the sources above):**

1. **Prompt size vs speed.** Every tool schema, every line of session notes
   and every history turn is prompt the model must process. On CPU that cost
   dominates [26][27][28]. Ollama reuses a cached prefix
   (`prompt_eval_cached_count` [1]), so the order matters. In today's
   `build_system()`, the persona is followed by `Now: <weekday HH:MM>` and
   then the session summary. The time changes every minute and the summary
   on most turns, so everything after them is recomputed. A local backend
   should keep persona, tools and few-shot examples stable at the front, and
   put time and session notes in the latest user turn (**inference from how
   prefix caching works**; check `prompt_eval_cached_count` in the logs to
   confirm).
2. **Context truncation.** With a 2–4k default context [5][9][10], a big tool
   list plus notes plus six history turns can silently overflow. Set
   `num_ctx` explicitly (8k is HA's default [32]).
3. **Cold loads.** The default 5-minute keep-alive means the first question
   after a quiet spell pays `load_duration` [1][5]. Use `-1`.
4. **Tool reliability.** Few tools, short descriptions, explicit "always call X
   when..." lines, argument repair, and a fallback.

## 5. Escalating from local to Claude

| Pattern | How it works | Evidence | Fit for Chip |
| --- | --- | --- | --- |
| **Router first (rules)** | Deterministic match before any LLM | HA "prefer handling commands locally" [35]; Chip already has `choose_model()` keyword routing | Best first layer. Zero latency. Route `WORK_WORDS` and session names to Claude, everything else to local. |
| **Learned router** | Classifier picks the strong or weak model per query | RouteLLM: "reduce costs by up to 85%" while keeping "95% GPT-4 performance" on MT Bench; trained on a GPT-4 / Mixtral pair, and thresholds must be calibrated on your own queries [39] | Overkill for one user. Needs labelled data. |
| **Cascade** | Try the cheap model, escalate if its answer fails a check | FrugalGPT: "match the performance of the best individual LLM (e.g. GPT-4) with up to 98% cost reduction" on their benchmarks [40] | Good when the check is mechanical (valid tool call, non-empty, under N s). |
| **Escalation tool** | Local model calls `ask_claude(question, why)` | No head-to-head benchmark found. Depends on the small model's tool reliability (§2, §4) | Good second layer. Lets the local model say "let me ask Claude" (natural filler while Claude works). |
| **Self-reported confidence** | Ask the model how sure it is | Kadavath et al. (Anthropic): larger models are well calibrated on multiple-choice, but "struggle with calibration of P(IK) on new tasks" [41] | Don't rely on it, especially with small models. |
| **Decision model as router** | Ollama's "System One" decision models return label probabilities plus a `confidence` [15] | Ollama: "A higher value does not guarantee the answer is correct" [15]. The models are 9b-class [19]. | Interesting, but a second model costs VRAM and latency. Not worth it at first. |

**Recommended shape (synthesis, not a benchmarked design):**

1. **Rules first.** Wake word, "mute", "watch X" and "ask Claude" are handled
   deterministically. Work questions and session names (the existing
   `choose_model` logic) go straight to Claude, because they need reasoning
   over session notes and Claude is what's good at that.
2. **Local model for everything else**, with a small tool set: `remember`,
   `set_focus`, `mute_mic`, `ask_claude`. Keep `find_ranges` and
   `adjust_range` Claude-only, since they move hardware and the current
   persona already restricts them to explicit requests.
3. **Automatic escalation on failure:** an unknown tool name, arguments that
   don't validate after repair, an empty reply, a local TTFT over about 3 s,
   or Joe saying "that's wrong". On escalation, Chip speaks a short holding
   line and sends Claude the same history plus the local model's attempt.
4. **Claude tier choice.** Claude Haiku 5.5 is "fastest" and meant for
   latency-sensitive routing-type work. Sonnet 5.5 is "fast" [42]. The
   Models API reports that Sonnet 5.5 rejects `thinking: {type: "disabled"}`
   but accepts `"between_tools"` [42]. Thinking can't be turned fully off on
   Sonnet 5.5, which matters for voice latency. Anthropic prompt caching
   needs at least 1,024 tokens (Sonnet/Opus) or 2,048 (Haiku) and lasts 5
   minutes by default [43]. Chip's prompt is likely below those minimums, so
   don't count on it.

## 6. "Teaching" the local model

**Tier 1: prompt and memory (do this first).**

- *System prompt.* The existing `PERSONA` is short and voice-oriented, which is
  right. Small models need more explicit rules, though. The HA builder fixed
  missed tool calls and rambling with per-tool "always call" lines and one
  example of the wanted answer [36].
- *Persistent memory file.* Add a `remember(fact)` tool that appends to a small
  file (for example `chip_memory.md`), and inject the file into the prompt.
  Keep it short: on CPU every memory line is prompt the model must process
  (§3). Claude can periodically condense it, as an escalation job.
- *Few-shot examples.* Send them as fixed `messages` at the start of each
  request, or bake them in with Modelfile `MESSAGE user|assistant ...` lines
  [10]. Either way they are prompt tokens, so keep 3–6 tight examples and keep
  them in the cacheable prefix.
- *Modelfile.* `FROM <model>`, `PARAMETER num_ctx 8192`,
  `PARAMETER temperature ...`, `SYSTEM """..."""`, `MESSAGE ...`, then
  `ollama create chip-local -f Modelfile` [10]. Because the Anthropic-compatible
  endpoint has no `num_ctx` field [12], the Modelfile is the clean place to fix
  the context size and sampling. I'd keep the persona text in the brain
  (one source of truth) rather than duplicating it in `SYSTEM`. Qwen
  publishes recommended non-thinking sampling (temperature 0.7, top_p 0.8,
  top_k 20, presence_penalty 1.5) [22].

**Tier 2: fine-tuning (only with evidence it's needed).**

- *What it takes.*
  - Unsloth recommends starting with QLoRA (4-bit). Its notebooks run "with
    just 3GB VRAM" (small models), and it installs on Windows, Linux or WSL
    [44].
  - Data: "a bare minimum of at least 100 rows", with "over 1,000 rows"
    preferable, in ChatML or ShareGPT chat format. For synthetic data, start
    from at least 10 real examples [45].
  - Training: 1–3 epochs [44]. The Ollama tutorial uses `max_steps = 60` for a
    quick run and says GGUF export takes "5 to 10 minutes" [46].
  - Unsloth doesn't publish wall-clock training times for a given GPU in these
    pages. I found no first-party number to quote.
- *Getting it into Ollama.* Current Ollama **rejects LoRA adapters**: the CLI
  and server both return "LoRA adapters are no longer supported". The
  `ADAPTER` docs and Ollama's in-tree safetensors-to-GGUF converter were
  removed in commit `98acec40` (#14969, 2026-09-14), which first shipped in
  v0.40.0 (not in v0.39.0) [16]. GGUF import now only wraps an existing,
  already-quantised GGUF ("Ollama does not quantize GGUF models during import")
  [11]. The path is: fine-tune → merge → export GGUF (Unsloth exports `Q8_0` or
  `q4_k_m` and writes a Modelfile [46]) → `FROM ./model.gguf` →
  `ollama create` [11]. Older guides that use `ADAPTER` no longer work.
- *When it's worth it:*
  - (a) The logs show a consistent style or format failure that prompting
    can't fix.
  - (b) You want to move a long persona and examples out of the prompt to
    save prompt processing on CPU.
  - (c) You have hundreds of logged examples of Chip's own tool calls to
    train on.
  - It is **not** the way to teach facts that change (sessions, Joe's
    preferences, today's tasks). Those belong in the memory file, which takes
    effect immediately.
- *Caution on "Claude as teacher".* Using logged Claude replies as training
  data is the obvious dataset. Check Anthropic's current commercial terms on
  using outputs to train models before doing that. I did not verify their
  wording for this report.

## Open questions / not verified

- Joe's actual hardware (GPU? VM? RAM?). Every latency here hinges on it.
- No first-hand benchmark of Ollama inside a Windows Hyper-V guest was found.
  §3(c) uses Linux cloud VMs [29].
- Small-model tool reliability on *Chip's* tools is untested. Vendor
  benchmarks (BFCL, tau2) measure other tasks, and vendors run them
  differently.
- Whether Ollama's new speech (Phonon) and transcription endpoints work on a
  Windows CPU-only install [13][14].
- Pages I could not open (access not granted in this session):
  `docs.claude.com/en/docs_site_map.md`, `ollama.com/library/qwen3/tags`,
  `ollama.com/library/llama3.2/tags`. The arXiv abstracts were read via their
  `v1` URLs.
- The Anthropic prompt-caching page [43] says cache reads cost 0.1× base input.
  The models overview says 5% on Sonnet 5.5 and Opus 5.5 [42]. The overview
  is newer; check pricing before relying on either.

## Sources

All accessed 2026-11-05. Ollama docs are cited from the `ollama/ollama` repo at
commit `6362e0a` (2026-11-05); the same pages are published at docs.ollama.com.

1. Ollama API reference, `docs/api.md`: https://github.com/ollama/ollama/blob/main/docs/api.md
2. Ollama, Tool calling, `docs/capabilities/tool-calling.mdx`: https://github.com/ollama/ollama/blob/main/docs/capabilities/tool-calling.mdx
3. Ollama, Streaming, `docs/capabilities/streaming.mdx`: https://github.com/ollama/ollama/blob/main/docs/capabilities/streaming.mdx
4. Ollama, Structured outputs, `docs/capabilities/structured-outputs.mdx`: https://github.com/ollama/ollama/blob/main/docs/capabilities/structured-outputs.mdx
5. Ollama FAQ, `docs/faq.mdx` (keep-alive, preload, context, concurrency, KV cache, env vars): https://github.com/ollama/ollama/blob/main/docs/faq.mdx
6. Ollama, Windows, `docs/windows.mdx`: https://github.com/ollama/ollama/blob/main/docs/windows.mdx
7. Ollama, Hardware support, `docs/gpu.mdx`: https://github.com/ollama/ollama/blob/main/docs/gpu.mdx
8. Ollama, Troubleshooting (LLM libraries / AVX), `docs/troubleshooting.mdx`: https://github.com/ollama/ollama/blob/main/docs/troubleshooting.mdx
9. Ollama, Context length, `docs/context-length.mdx`: https://github.com/ollama/ollama/blob/main/docs/context-length.mdx
10. Ollama, Modelfile reference, `docs/modelfile.mdx`: https://github.com/ollama/ollama/blob/main/docs/modelfile.mdx
11. Ollama, Importing a model, `docs/import.mdx`: https://github.com/ollama/ollama/blob/main/docs/import.mdx
12. Ollama, Anthropic compatibility, `docs/api/anthropic-compatibility.mdx`: https://github.com/ollama/ollama/blob/main/docs/api/anthropic-compatibility.mdx
13. Ollama, OpenAI compatibility (audio endpoints), `docs/api/openai-compatibility.mdx`: https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx
14. Ollama, Speech, `docs/capabilities/speech.mdx` (and its commit history, Oct 2026): https://github.com/ollama/ollama/blob/main/docs/capabilities/speech.mdx
15. Ollama, Decision, `docs/capabilities/decision.mdx`: https://github.com/ollama/ollama/blob/main/docs/capabilities/decision.mdx
16. Ollama source: `cmd/cmd.go` and `server/create.go` (`errAdaptersUnsupported`); commit 98acec40, "create: add server-side MLX imports and drop GGUF conversion (#14969)", first tagged in v0.40.0: https://github.com/ollama/ollama/commit/98acec40ae2b3ed361fc5117e5b2ae81a4bf5c18
17. Ollama blog, "Streaming responses with tool calling", 2025-05-28: https://ollama.com/blog/streaming-tool
18. Ollama model library listing: https://ollama.com/library
19. Ollama search, tools filter: https://ollama.com/search?c=tools
20. Ollama, qwen3.5 tags: https://ollama.com/library/qwen3.5/tags (model page: https://ollama.com/library/qwen3.5)
21. Ollama, gemma4 model page and tags: https://ollama.com/library/gemma4 , https://ollama.com/library/gemma4/tags
22. Qwen, Qwen3.5-9B model card (Hugging Face): https://huggingface.co/Qwen/Qwen3.5-9B
23. Google, gemma-4-E4B-it model card (Hugging Face): https://huggingface.co/google/gemma-4-E4B-it
24. Meta, Llama 3.2 model card: https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/MODEL_CARD.md
25. llama.cpp discussion #15013, "Performance of llama.cpp on Nvidia CUDA": https://github.com/ggml-org/llama.cpp/discussions/15013
26. LocalScore (Mozilla Builders): leaderboard https://www.localscore.ai/ , test method https://www.localscore.ai/about , latest results https://www.localscore.ai/latest , repo https://github.com/cjpais/LocalScore
27. OpenBenchmarking.org result 2509087-NE-9950X3DLL86 (Ryzen 9 9950X3D, llama.cpp CPU): https://mail.openbenchmarking.org/result/2509087-NE-9950X3DLL86&export=html
28. Marcus Groeber, "Benchmarking llama.cpp on legacy hardware" (2026-02-02, updated 2026-08-20): https://www.mgroeber.de/benchmarking-llamacpp-on-legacy-hardware-2.html
29. Hasan Aboul Hasan, "Self-Host an LLM with Ollama on a VPS: Real Numbers From 6 Servers" (2026-08-12): https://learnwithhasan.com/guide/self-host-ollama/
30. Microsoft Learn, GPU partitioning (Windows Server 2025): https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/gpu-partitioning
31. Microsoft Learn, Processor compatibility mode in Hyper-V: https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/manage/processor-compatibility-mode-hyper-v
32. Home Assistant, Ollama integration docs: https://www.home-assistant.io/integrations/ollama/
33. Home Assistant core source, `homeassistant/components/ollama/const.py` and `entity.py` (commit 68a3246, 2026-11-05): https://github.com/home-assistant/core/tree/dev/homeassistant/components/ollama
34. Home Assistant, Local voice assistant: https://www.home-assistant.io/voice_control/voice_remote_local_assistant/
35. Home Assistant, AI personality / prefer handling commands locally: https://www.home-assistant.io/voice_control/assist_create_open_ai_personality/
36. Nicolas Mowen (crzynik), "My Journey to a reliable and enjoyable locally hosted voice assistant", HA Community, 2025-10-27 (edited since): https://community.home-assistant.io/t/my-journey-to-a-reliable-and-enjoyable-locally-hosted-voice-assistant/944860
37. SYSTRAN, faster-whisper README (benchmarks, CUDA requirements): https://github.com/SYSTRAN/faster-whisper
38. Open Home Foundation, Piper (piper1-gpl): https://github.com/OHF-Voice/piper1-gpl
39. LMSYS, RouteLLM: https://github.com/lm-sys/RouteLLM
40. Chen, Zaharia, Zou, "FrugalGPT" (arXiv 2305.05176, 2023): https://arxiv.org/abs/2305.05176v1
41. Kadavath et al., "Language Models (Mostly) Know What They Know" (arXiv 2207.05221, 2022): https://arxiv.org/abs/2207.05221v1
42. Anthropic, Models overview: https://platform.claude.com/docs/en/about-claude/models/overview.md
43. Anthropic, Prompt caching: https://docs.claude.com/en/docs/build-with-claude/prompt-caching
44. Unsloth, Fine-tuning LLMs Guide: https://unsloth.ai/docs/get-started/fine-tuning-llms-guide
45. Unsloth, Datasets Guide: https://unsloth.ai/docs/get-started/fine-tuning-llms-guide/datasets-guide
46. Unsloth, Tutorial: How to Finetune Llama-3 and Use In Ollama: https://unsloth.ai/docs/get-started/fine-tuning-llms-guide/tutorial-how-to-finetune-llama-3-and-use-in-ollama
