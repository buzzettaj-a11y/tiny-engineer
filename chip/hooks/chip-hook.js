#!/usr/bin/env node
// Chip hook forwarder: Claude Code hook JSON (stdin) -> POST <brain>/events.
//
// Zero dependencies. Never blocks or breaks Claude Code: everything is wrapped,
// the whole run is capped at 1.5 s, and it ALWAYS exits 0 without output.
//
// Brain URL: env CHIP_BRAIN_URL, else chip-hook.json next to this file:
//   {"brain_url": "http://home-pc:47101", "token": ""}
// Optional shared secret: env CHIP_BRAIN_TOKEN or "token" in that file.
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");

const TIMEOUT_MS = 1500;
const RECEIVED_FROM = "chip-hook/1";
const CONFIG_NAME = "chip-hook.json";

const TOOL_INPUT_KEEP = ["file_path", "notebook_path", "path", "command", "pattern", "glob", "query", "url",
  "description", "prompt", "subagent_type"];

function trimString(s, n) {
  if (typeof s !== "string") return s;
  return s.length <= n ? s : `${s.slice(0, n)}… (+${s.length - n} chars)`;
}

function trimToolInput(ti) {
  if (ti == null) return ti;
  if (typeof ti !== "object") return trimString(String(ti), 300);
  const out = {};
  for (const key of TOOL_INPUT_KEEP) {
    if (ti[key] !== undefined) {
      out[key] = typeof ti[key] === "string" ? trimString(ti[key], 200) : ti[key];
    }
  }
  // Size hints instead of big payloads (file contents, diffs).
  for (const [key, val] of Object.entries(ti)) {
    if (key in out) continue;
    if (typeof val === "string") out[key] = val.length > 120 ? `<${val.length} chars>` : val;
    else if (typeof val === "number" || typeof val === "boolean") out[key] = val;
    else if (Array.isArray(val)) out[key] = `<${val.length} items>`;
  }
  let json = JSON.stringify(out);
  if (json.length > 600) {
    for (const key of Object.keys(out)) {
      if (!TOOL_INPUT_KEEP.includes(key)) delete out[key];
    }
  }
  return out;
}

function summarize(v, n) {
  if (v == null) return v;
  if (typeof v === "string") return trimString(v, n);
  try {
    return trimString(JSON.stringify(v), n);
  } catch {
    return undefined;
  }
}

/** Add host/received_from and shrink big fields. */
function trimEvent(ev, host) {
  const out = {};
  for (const [key, val] of Object.entries(ev || {})) {
    if (typeof val === "string") out[key] = trimString(val, 1000);
    else if (val && typeof val === "object") out[key] = summarize(val, 600);
    else out[key] = val;
  }
  if ("tool_input" in ev) out.tool_input = trimToolInput(ev.tool_input);
  if ("tool_response" in ev) out.tool_response = summarize(ev.tool_response, 300);
  for (const [key, n] of [["prompt", 500], ["message", 300], ["last_assistant_message", 300], ["error", 300]]) {
    if (typeof ev[key] === "string") out[key] = trimString(ev[key], n);
  }
  out.host = host || os.hostname();
  out.received_from = RECEIVED_FROM;
  return out;
}

function loadConfig(dir, env) {
  const cfg = { brain_url: "", token: "" };
  try {
    const file = JSON.parse(fs.readFileSync(path.join(dir, CONFIG_NAME), "utf8"));
    if (file && typeof file === "object") {
      if (typeof file.brain_url === "string") cfg.brain_url = file.brain_url;
      if (typeof file.token === "string") cfg.token = file.token;
    }
  } catch {
    // no config file is fine when env is set
  }
  if (env.CHIP_BRAIN_URL) cfg.brain_url = env.CHIP_BRAIN_URL;
  if (env.CHIP_BRAIN_TOKEN) cfg.token = env.CHIP_BRAIN_TOKEN;
  return cfg;
}

function eventsUrl(base) {
  const b = String(base || "").trim().replace(/\/+$/, "");
  if (!b) return "";
  const withScheme = /^https?:\/\//i.test(b) ? b : `http://${b}`;
  return withScheme.endsWith("/events") ? withScheme : `${withScheme}/events`;
}

function post(url, payload, token, timeoutMs) {
  return new Promise((resolve) => {
    try {
      const u = new URL(url);
      const body = Buffer.from(JSON.stringify(payload));
      const headers = { "Content-Type": "application/json", "Content-Length": body.length };
      if (token) headers["X-Chip-Token"] = token;
      const req = (u.protocol === "https:" ? https : http).request(
        u, { method: "POST", headers, timeout: timeoutMs }, (res) => {
          res.resume();
          res.on("end", () => resolve(res.statusCode));
          res.on("error", () => resolve(0));
        });
      req.on("timeout", () => { req.destroy(); resolve(0); });
      req.on("error", () => resolve(0));
      req.end(body);
    } catch {
      resolve(0);
    }
  });
}

function readStdin() {
  return new Promise((resolve) => {
    if (process.stdin.isTTY) return resolve("");
    const chunks = [];
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", () => resolve(""));
  });
}

async function main() {
  const started = Date.now();
  const text = await readStdin();
  let ev;
  try {
    ev = JSON.parse(text);
  } catch {
    return;
  }
  if (!ev || typeof ev !== "object" || typeof ev.hook_event_name !== "string") return;
  const cfg = loadConfig(__dirname, process.env);
  const url = eventsUrl(cfg.brain_url);
  if (!url) return;
  const left = Math.max(100, TIMEOUT_MS - (Date.now() - started) - 50);
  await post(url, trimEvent(ev), cfg.token, left);
}

if (require.main === module) {
  const bye = () => process.exit(0);
  process.on("uncaughtException", bye);
  process.on("unhandledRejection", bye);
  setTimeout(bye, TIMEOUT_MS); // hard cap, whatever happens
  main().then(bye, bye);
} else {
  module.exports = { trimEvent, trimToolInput, loadConfig, eventsUrl, RECEIVED_FROM, TIMEOUT_MS };
}
