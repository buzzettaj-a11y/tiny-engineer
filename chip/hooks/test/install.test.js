"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const INSTALL = path.join(__dirname, "..", "install.js");
const { CLAUDE_EVENTS, applyInstall, applyUninstall, hookCommand, main } = require(INSTALL);

function tmpHome(settings) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "chiphome-"));
  if (settings !== undefined) {
    fs.mkdirSync(path.join(home, ".claude"));
    fs.writeFileSync(path.join(home, ".claude", "settings.json"),
      typeof settings === "string" ? settings : JSON.stringify(settings, null, 2));
  }
  return home;
}

const read = (home) => JSON.parse(fs.readFileSync(path.join(home, ".claude", "settings.json"), "utf8"));
const backups = (home) => fs.readdirSync(path.join(home, ".claude")).filter((f) => f.includes("chip-backup"));
const quiet = { log: () => {} };

const existing = {
  model: "opus",
  permissions: { allow: ["Bash(npm test)"] },
  hooks: {
    Stop: [
      { hooks: [{ type: "command", command: "npx tiny-engineer hook claude-code", async: true }] },
      { hooks: [{ type: "command", command: "say done" }] },
    ],
    PreToolUse: [
      {
        matcher: "Bash",
        hooks: [
          { type: "command", command: "my-linter" },
          { type: "command", command: "tiny-engineer-claude-code --url http://192.168.86.223" },
        ],
      },
    ],
    SessionStart: [{ hooks: [{ type: "command", command: "node /x/tiny-engineer/bin/tiny-engineer.js hook claude-code" }] }],
  },
};

test("applyInstall adds one entry per event, removes tiny-engineer, keeps the rest", () => {
  const { settings, removedTiny } = applyInstall(existing, 'node "C:/Users/joe/.claude/chip/chip-hook.js"');
  assert.equal(removedTiny, 3);
  assert.equal(settings.model, "opus");
  assert.deepEqual(settings.permissions, existing.permissions);
  for (const ev of CLAUDE_EVENTS) {
    const ours = settings.hooks[ev].flatMap((w) => w.hooks).filter((h) => h.command.includes("chip-hook.js"));
    assert.equal(ours.length, 1, ev);
    assert.equal(ours[0].type, "command");
    assert.equal(ours[0].async, true);
  }
  const all = JSON.stringify(settings);
  assert.ok(!all.includes("hook claude-code") && !all.includes("tiny-engineer-claude-code"));
  assert.ok(all.includes("say done") && all.includes("my-linter"));
  assert.equal(settings.hooks.PreToolUse[0].matcher, "Bash");
  // idempotent: re-install replaces, never duplicates
  const again = applyInstall(settings, 'node "C:/Users/joe/.claude/chip/chip-hook.js"').settings;
  assert.deepEqual(again, settings);
  // original untouched
  assert.equal(existing.hooks.Stop.length, 2);
});

test("applyInstall can keep tiny-engineer hooks", () => {
  const { settings, removedTiny } = applyInstall(existing, "node chip-hook.js", { removeTinyEngineer: false });
  assert.equal(removedTiny, 0);
  assert.ok(JSON.stringify(settings).includes("hook claude-code"));
});

test("applyUninstall removes only Chip entries", () => {
  const installed = applyInstall(existing, "node chip-hook.js").settings;
  const { settings, removed } = applyUninstall(installed);
  assert.equal(removed, CLAUDE_EVENTS.length);
  assert.ok(!JSON.stringify(settings).includes("chip-hook.js"));
  assert.ok(JSON.stringify(settings).includes("say done"));
  assert.equal(settings.hooks.SessionStart, undefined);  // emptied events disappear
});

test("hookCommand uses forward slashes and quotes the path", () => {
  const p = ["C:", "Users", "Joe Smith", ".claude", "chip", "chip-hook.js"].join(path.sep);
  assert.equal(hookCommand(p), 'node "C:/Users/Joe Smith/.claude/chip/chip-hook.js"');
});

test("install end to end: copies hook, saves URL, backs up, rewrites settings", async () => {
  const home = tmpHome(existing);
  const code = await main(["--home", home, "--url", "http://joe-pc.tailnet.ts.net:47101", "--token", "t1",
    "--no-ping"], quiet);
  assert.equal(code, 0);
  const s = read(home);
  assert.equal(s.model, "opus");
  assert.ok(!JSON.stringify(s).includes("tiny-engineer"));
  const dest = path.join(home, ".claude", "chip", "chip-hook.js");
  assert.ok(fs.existsSync(dest));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(home, ".claude", "chip", "chip-hook.json"), "utf8")),
    { brain_url: "http://joe-pc.tailnet.ts.net:47101", token: "t1" });
  assert.equal(s.hooks.Stop.at(-1).hooks[0].command, hookCommand(dest));
  assert.equal(backups(home).length, 1);
  const backup = JSON.parse(fs.readFileSync(path.join(home, ".claude", backups(home)[0]), "utf8"));
  assert.deepEqual(backup, existing);

  // the installed copy really runs (CommonJS marker beats any "type": "module" above)
  const r = spawnSync(process.execPath, [dest], { input: '{"hook_event_name":"Stop"}', timeout: 5000,
    env: { ...process.env, CHIP_BRAIN_URL: "http://127.0.0.1:9" } });
  assert.equal(r.status, 0);
  assert.equal(String(r.stdout) + String(r.stderr), "");

  // re-run without --url reuses the saved URL; nothing changes -> no new backup
  assert.equal(await main(["--home", home, "--no-ping"], quiet), 0);
  assert.equal(backups(home).length, 1);

  assert.equal(await main(["--home", home, "--uninstall"], quiet), 0);
  assert.ok(!JSON.stringify(read(home)).includes("chip-hook.js"));
  assert.equal(read(home).model, "opus");
});

test("fresh machine without settings.json", async () => {
  const home = tmpHome();
  assert.equal(await main(["--home", home, "--url", "http://100.64.0.5:47101", "--no-ping"], quiet), 0);
  assert.equal(Object.keys(read(home).hooks).length, CLAUDE_EVENTS.length);
  assert.equal(backups(home).length, 0);
});

test("dry run writes nothing", async () => {
  const home = tmpHome(existing);
  const before = fs.readFileSync(path.join(home, ".claude", "settings.json"), "utf8");
  const lines = [];
  assert.equal(await main(["--home", home, "--url", "http://pc:47101", "--dry-run"], { log: (l) => lines.push(l) }), 0);
  assert.equal(fs.readFileSync(path.join(home, ".claude", "settings.json"), "utf8"), before);
  assert.ok(!fs.existsSync(path.join(home, ".claude", "chip")));
  assert.ok(lines.some((l) => l.includes("tiny-engineer hook entries removed: 3")));
});

test("refuses broken settings.json and bad arguments", async () => {
  const home = tmpHome("{ not json");
  assert.equal(await main(["--home", home, "--url", "http://pc:47101", "--no-ping"], quiet), 1);
  assert.equal(fs.readFileSync(path.join(home, ".claude", "settings.json"), "utf8"), "{ not json");
  const h2 = tmpHome({});
  assert.equal(await main(["--home", h2, "--no-ping"], quiet), 2);  // no URL yet
  assert.equal(await main(["--home", h2, "--url", "pc 47101", "--no-ping"], quiet), 2);
  assert.equal(await main(["--bogus"], quiet), 2);
});

test("--clean-project strips tiny-engineer hooks from a project", async () => {
  const home = tmpHome({});
  const proj = fs.mkdtempSync(path.join(os.tmpdir(), "proj-"));
  fs.mkdirSync(path.join(proj, ".claude"));
  fs.writeFileSync(path.join(proj, ".claude", "settings.json"), JSON.stringify(existing));
  assert.equal(await main(["--home", home, "--url", "http://pc:47101", "--clean-project", proj, "--no-ping"], quiet), 0);
  const p = JSON.parse(fs.readFileSync(path.join(proj, ".claude", "settings.json"), "utf8"));
  assert.ok(!JSON.stringify(p).includes("tiny-engineer"));
  assert.ok(JSON.stringify(p).includes("my-linter"));
  assert.ok(!JSON.stringify(p).includes("chip-hook.js"));  // project gets no Chip hooks (user-level does)
});

test("CLAUDE_EVENTS matches the tiny-engineer integration list", async () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "..", "packages", "tiny-engineer-cli", "src",
    "integrations", "claude-code", "install.js"), "utf8");
  const block = src.match(/CLAUDE_EVENTS = \[([\s\S]*?)\]/)[1];
  const theirs = [...block.matchAll(/"(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(CLAUDE_EVENTS, theirs);
});
