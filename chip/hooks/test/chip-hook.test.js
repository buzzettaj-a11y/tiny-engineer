"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const os = require("node:os");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const HOOK = path.join(__dirname, "..", "chip-hook.js");
const { trimEvent, eventsUrl, loadConfig } = require(HOOK);

function runHook(stdin, env = {}, cwd = undefined, script = HOOK) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const child = spawn(process.execPath, [script], {
      env: { ...process.env, CHIP_BRAIN_URL: "", CHIP_BRAIN_TOKEN: "", ...env },
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("close", (code) => resolve({ code, out, ms: Date.now() - t0 }));
    if (stdin !== null) child.stdin.end(stdin);
  });
}

function server(handler) {
  return new Promise((resolve) => {
    const received = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        received.push({ method: req.method, url: req.url, headers: req.headers, body });
        handler(req, res);
      });
    });
    srv.listen(0, "127.0.0.1", () => resolve({ srv, received, url: `http://127.0.0.1:${srv.address().port}` }));
  });
}

test("trimEvent adds host and received_from, trims big fields", () => {
  const ev = {
    hook_event_name: "PreToolUse",
    session_id: "abc",
    cwd: "C:\\code\\JobSearch",
    tool_name: "Write",
    tool_input: { file_path: "C:\\code\\JobSearch\\big.py", content: "x".repeat(50000) },
    tool_response: { output: "y".repeat(5000) },
    prompt: "p".repeat(2000),
  };
  const out = trimEvent(ev, "VM-2");
  assert.equal(out.host, "VM-2");
  assert.equal(out.received_from, "chip-hook/1");
  assert.equal(out.tool_input.file_path, "C:\\code\\JobSearch\\big.py");
  assert.equal(out.tool_input.content, "<50000 chars>");
  assert.ok(typeof out.tool_response === "string" && out.tool_response.length < 350);
  assert.ok(out.prompt.length < 560);
  assert.ok(JSON.stringify(out).length < 2500);
  assert.equal(trimEvent({ hook_event_name: "Stop" }).host, os.hostname());
});

test("trimEvent keeps Bash commands short", () => {
  const out = trimEvent({ hook_event_name: "PreToolUse", tool_input: { command: "echo " + "a".repeat(900) } }, "h");
  assert.ok(out.tool_input.command.startsWith("echo aaa"));
  assert.ok(out.tool_input.command.length < 240);
});

test("eventsUrl and loadConfig", () => {
  assert.equal(eventsUrl("http://pc:47101"), "http://pc:47101/events");
  assert.equal(eventsUrl("http://pc:47101/"), "http://pc:47101/events");
  assert.equal(eventsUrl("pc.tail1234.ts.net:47101"), "http://pc.tail1234.ts.net:47101/events");
  assert.equal(eventsUrl("http://pc:47101/events"), "http://pc:47101/events");
  assert.equal(eventsUrl(""), "");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "chiphook-"));
  fs.writeFileSync(path.join(dir, "chip-hook.json"), JSON.stringify({ brain_url: "http://a:1", token: "t" }));
  assert.deepEqual(loadConfig(dir, {}), { brain_url: "http://a:1", token: "t" });
  assert.equal(loadConfig(dir, { CHIP_BRAIN_URL: "http://b:2" }).brain_url, "http://b:2");
  assert.deepEqual(loadConfig(path.join(dir, "missing"), {}), { brain_url: "", token: "" });
});

test("forwards the hook JSON to the brain and exits 0 silently", async () => {
  const s = await server((req, res) => res.end('{"ok":true}'));
  try {
    const ev = { hook_event_name: "Stop", session_id: "s1", cwd: "/x/Flip" };
    const r = await runHook(JSON.stringify(ev), { CHIP_BRAIN_URL: s.url, CHIP_BRAIN_TOKEN: "tok" });
    assert.equal(r.code, 0);
    assert.equal(r.out, "");
    assert.equal(s.received.length, 1);
    const got = s.received[0];
    assert.equal(got.method, "POST");
    assert.equal(got.url, "/events");
    assert.equal(got.headers["content-type"], "application/json");
    assert.equal(got.headers["x-chip-token"], "tok");
    const body = JSON.parse(got.body);
    assert.equal(body.hook_event_name, "Stop");
    assert.equal(body.session_id, "s1");
    assert.equal(body.host, os.hostname());
    assert.equal(body.received_from, "chip-hook/1");
  } finally {
    s.srv.close();
  }
});

test("uses chip-hook.json next to the script", async () => {
  const s = await server((req, res) => res.end("{}"));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "chiphook-"));
  fs.copyFileSync(HOOK, path.join(dir, "chip-hook.js"));
  fs.writeFileSync(path.join(dir, "package.json"), '{"type":"commonjs"}');
  fs.writeFileSync(path.join(dir, "chip-hook.json"), JSON.stringify({ brain_url: s.url }));
  try {
    const r = await runHook('{"hook_event_name":"SessionStart","source":"startup"}', {}, dir,
      path.join(dir, "chip-hook.js"));
    assert.equal(r.code, 0);
    assert.equal(s.received.length, 1);
  } finally {
    s.srv.close();
  }
});

test("hanging brain: gives up within ~1.5 s, still exits 0", async () => {
  const s = await server(() => { /* never answers */ });
  try {
    const r = await runHook('{"hook_event_name":"Stop"}', { CHIP_BRAIN_URL: s.url });
    assert.equal(r.code, 0);
    assert.equal(r.out, "");
    assert.ok(r.ms < 2500, `took ${r.ms} ms`);
  } finally {
    s.srv.closeAllConnections?.();
    s.srv.close();
  }
});

test("brain down, bad JSON, empty stdin, no URL, error status: all exit 0 silently", async () => {
  const dead = "http://127.0.0.1:9";
  for (const [stdin, env] of [
    ['{"hook_event_name":"Stop"}', { CHIP_BRAIN_URL: dead }],
    ["not json", { CHIP_BRAIN_URL: dead }],
    ["", { CHIP_BRAIN_URL: dead }],
    ['{"no_event":1}', { CHIP_BRAIN_URL: dead }],
    ['{"hook_event_name":"Stop"}', {}],
    ['{"hook_event_name":"Stop"}', { CHIP_BRAIN_URL: "::not a url::" }],
  ]) {
    const r = await runHook(stdin, env, os.tmpdir());
    assert.equal(r.code, 0, `stdin=${stdin}`);
    assert.equal(r.out, "");
  }
  const s = await server((req, res) => { res.statusCode = 500; res.end("boom"); });
  try {
    const r = await runHook('{"hook_event_name":"Stop"}', { CHIP_BRAIN_URL: s.url });
    assert.equal(r.code, 0);
    assert.equal(r.out, "");
  } finally {
    s.srv.close();
  }
});

test("stdin that never closes is cut off at the hard cap", async () => {
  const child = spawn(process.execPath, [HOOK], { env: { ...process.env, CHIP_BRAIN_URL: "http://127.0.0.1:9" } });
  const t0 = Date.now();
  child.stdin.write('{"hook_event_name":');
  const code = await new Promise((resolve) => child.on("close", resolve));
  assert.equal(code, 0);
  assert.ok(Date.now() - t0 < 2500);
});
