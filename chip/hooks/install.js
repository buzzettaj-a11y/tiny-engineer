#!/usr/bin/env node
// Install Chip's Claude Code hooks for this user (all projects).
//
//   node install.js --url http://HOME-PC:47101          install / update
//   node install.js --url http://HOME-PC:47101 --dry-run   show, change nothing
//   node install.js --uninstall                         remove Chip's hooks
//
// What it does:
//   1. copies chip-hook.js to ~/.claude/chip/ and saves the brain URL next to it
//   2. backs up ~/.claude/settings.json, then adds one hook entry per event
//   3. removes old tiny-engineer hook entries, so the robot is driven only by
//      the brain (use --keep-tiny-engineer to leave them)
// Other options: --token <secret>, --settings <path>, --home <dir>,
//   --clean-project <dir> (also strip tiny-engineer hooks from a project's
//   .claude/settings*.json), --no-ping.
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");

// Keep in sync with packages/tiny-engineer-cli/src/integrations/claude-code/install.js
const CLAUDE_EVENTS = [
  "SessionStart",
  "SessionEnd",
  "UserPromptSubmit",
  "SubagentStart",
  "PreToolUse",
  "PostToolBatch",
  "PostToolUseFailure",
  "PreCompact",
  "PermissionRequest",
  "PermissionDenied",
  "Notification",
  "Stop",
  "StopFailure",
];
const TINY_ENGINEER_MARKERS = ["hook claude-code", "tiny-engineer-claude-code"];
const CHIP_MARKER = "chip-hook.js";

const isTinyEngineerCommand = (c) => typeof c === "string" && TINY_ENGINEER_MARKERS.some((m) => c.includes(m));
const isChipCommand = (c) => typeof c === "string" && c.includes(CHIP_MARKER);

/** Remove hook commands matching `pred`; drop wrappers/events left empty. */
function removeHooks(settings, pred) {
  const root = settings && typeof settings === "object" ? structuredClone(settings) : {};
  let removed = 0;
  if (!root.hooks || typeof root.hooks !== "object") return { settings: root, removed };
  const next = {};
  for (const [event, block] of Object.entries(root.hooks)) {
    if (!Array.isArray(block)) {
      next[event] = block;
      continue;
    }
    const kept = [];
    for (const wrapper of block) {
      if (!wrapper || typeof wrapper !== "object" || !Array.isArray(wrapper.hooks)) {
        kept.push(wrapper);
        continue;
      }
      const inner = wrapper.hooks.filter((h) => {
        const ours = pred(h && h.command);
        if (ours) removed++;
        return !ours;
      });
      if (inner.length > 0) kept.push({ ...wrapper, hooks: inner });
    }
    if (kept.length > 0) next[event] = kept;
  }
  root.hooks = next;
  return { settings: root, removed };
}

function hookCommand(scriptPath) {
  // Forward slashes work in cmd, PowerShell, Git Bash and node on Windows.
  return `node "${scriptPath.split(path.sep).join("/")}"`;
}

function applyInstall(settings, command, { removeTinyEngineer = true } = {}) {
  let { settings: root, removed: oldChip } = removeHooks(settings, isChipCommand);
  let removedTiny = 0;
  if (removeTinyEngineer) {
    const r = removeHooks(root, isTinyEngineerCommand);
    root = r.settings;
    removedTiny = r.removed;
  }
  root.hooks = root.hooks && typeof root.hooks === "object" ? root.hooks : {};
  for (const event of CLAUDE_EVENTS) {
    const list = Array.isArray(root.hooks[event]) ? root.hooks[event] : [];
    list.push({ hooks: [{ type: "command", command, async: true, timeout: 5 }] });
    root.hooks[event] = list;
  }
  return { settings: root, removedTiny, replacedChip: oldChip };
}

function applyUninstall(settings) {
  return removeHooks(settings, isChipCommand);
}

function readJson(file) {
  if (!fs.existsSync(file)) return { data: null, raw: null };
  const raw = fs.readFileSync(file, "utf8");
  if (!raw.trim()) return { data: {}, raw };
  return { data: JSON.parse(raw), raw }; // throws on broken JSON: never clobber it
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function writeWithBackup(file, raw, data, log) {
  const text = `${JSON.stringify(data, null, 2)}\n`;
  if (raw === text) return null;
  let backup = null;
  if (raw !== null) {
    backup = `${file}.chip-backup-${stamp()}`;
    fs.copyFileSync(file, backup);
    log(`  backup: ${backup}`);
  }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
  return backup;
}

function parseArgs(argv) {
  const a = { url: "", token: undefined, dryRun: false, uninstall: false, settings: "", home: "",
    keepTiny: false, cleanProjects: [], ping: true, help: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const val = () => {
      if (i + 1 >= argv.length) throw new Error(`${k} needs a value`);
      return argv[++i];
    };
    if (k === "--url") a.url = val();
    else if (k === "--token") a.token = val();
    else if (k === "--dry-run") a.dryRun = true;
    else if (k === "--uninstall") a.uninstall = true;
    else if (k === "--settings") a.settings = val();
    else if (k === "--home") a.home = val();
    else if (k === "--keep-tiny-engineer") a.keepTiny = true;
    else if (k === "--clean-project") a.cleanProjects.push(val());
    else if (k === "--no-ping") a.ping = false;
    else if (k === "-h" || k === "--help") a.help = true;
    else throw new Error(`unknown option ${k}`);
  }
  return a;
}

function ping(url, timeoutMs = 2000) {
  return new Promise((resolve) => {
    try {
      const u = new URL(url.replace(/\/+$/, "").replace(/\/events$/, "") + "/status");
      const req = (u.protocol === "https:" ? https : http).get(u, { timeout: timeoutMs }, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on("timeout", () => { req.destroy(); resolve(false); });
      req.on("error", () => resolve(false));
    } catch {
      resolve(false);
    }
  });
}

async function main(argv, { log = console.log } = {}) {
  let args;
  try {
    args = parseArgs(argv);
  } catch (e) {
    log(`error: ${e.message}`);
    return 2;
  }
  if (args.help) {
    log(fs.readFileSync(__filename, "utf8").split("\n").slice(1, 18).map((l) => l.replace(/^\/\/ ?/, "")).join("\n"));
    return 0;
  }
  const home = args.home || os.homedir();
  const claudeDir = path.join(home, ".claude");
  const settingsFile = args.settings || path.join(claudeDir, "settings.json");
  const installDir = path.join(claudeDir, "chip");
  const hookDest = path.join(installDir, "chip-hook.js");
  const cfgDest = path.join(installDir, "chip-hook.json");
  const dry = args.dryRun ? "[dry run] " : "";

  let current;
  try {
    current = readJson(settingsFile);
  } catch (e) {
    log(`error: ${settingsFile} is not valid JSON (${e.message}). Fix it first; nothing changed.`);
    return 1;
  }

  if (args.uninstall) {
    const { settings, removed } = applyUninstall(current.data);
    log(`${dry}removing ${removed} Chip hook entr${removed === 1 ? "y" : "ies"} from ${settingsFile}`);
    if (!args.dryRun && current.raw !== null && removed > 0) writeWithBackup(settingsFile, current.raw, settings, log);
    return 0;
  }

  let saved = {};
  try {
    saved = JSON.parse(fs.readFileSync(cfgDest, "utf8"));
  } catch {
    // first install
  }
  const url = (args.url || saved.brain_url || "").trim();
  if (!url) {
    log("error: pass the brain address, e.g. --url http://HOME-PC:47101 (Tailscale name or IP)");
    return 2;
  }
  if (!/^https?:\/\/[^/\s]+/i.test(url)) {
    log(`error: --url must look like http://HOST:47101 (got "${url}")`);
    return 2;
  }
  const token = args.token !== undefined ? args.token : saved.token || "";
  const command = hookCommand(hookDest);
  const { settings, removedTiny, replacedChip } = applyInstall(current.data, command,
    { removeTinyEngineer: !args.keepTiny });

  log(`${dry}hook script : ${hookDest}`);
  log(`${dry}brain URL   : ${url}`);
  log(`${dry}settings    : ${settingsFile}`);
  log(`${dry}events      : ${CLAUDE_EVENTS.length} (${replacedChip ? "updated" : "new"})`);
  log(`${dry}tiny-engineer hook entries removed: ${removedTiny}`);

  const projectChanges = [];
  for (const dir of args.cleanProjects) {
    for (const name of ["settings.json", "settings.local.json"]) {
      const file = path.join(dir, ".claude", name);
      let cur;
      try {
        cur = readJson(file);
      } catch (e) {
        log(`  skip ${file}: invalid JSON`);
        continue;
      }
      if (cur.data === null) continue;
      const r = removeHooks(cur.data, isTinyEngineerCommand);
      if (r.removed > 0) {
        log(`${dry}${file}: removing ${r.removed} tiny-engineer hook entr${r.removed === 1 ? "y" : "ies"}`);
        projectChanges.push([file, cur.raw, r.settings]);
      }
    }
  }

  if (args.dryRun) {
    log("\nresulting settings.json:\n" + JSON.stringify(settings, null, 2));
    return 0;
  }

  fs.mkdirSync(installDir, { recursive: true });
  fs.copyFileSync(path.join(__dirname, "chip-hook.js"), hookDest);
  // CommonJS marker, so a "type": "module" package.json higher up can't break it
  fs.writeFileSync(path.join(installDir, "package.json"), '{ "type": "commonjs", "private": true }\n');
  fs.writeFileSync(cfgDest, `${JSON.stringify({ brain_url: url, token }, null, 2)}\n`);
  writeWithBackup(settingsFile, current.raw, settings, log);
  for (const [file, raw, data] of projectChanges) writeWithBackup(file, raw, data, log);

  log("\nDone. New Claude Code sessions will report to Chip.");
  if (args.ping) {
    const ok = await ping(url);
    log(ok ? `Brain answered at ${url}.` : `Note: brain not reachable at ${url} yet (is it running? firewall? Tailscale?).`);
  }
  return 0;
}

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (e) => {
    console.error(e);
    process.exit(1);
  });
} else {
  module.exports = { CLAUDE_EVENTS, TINY_ENGINEER_MARKERS, isTinyEngineerCommand, isChipCommand, applyInstall,
    applyUninstall, removeHooks, hookCommand, parseArgs, main };
}
