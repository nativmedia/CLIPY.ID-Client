// Exercise the real UI controller with a tiny DOM and a mocked backend. No project data is used.
const assert = require("node:assert/strict");
const path = require("node:path");
const Module = require("node:module");
const { test } = require("node:test");
const esbuild = require("esbuild");

async function loadController() {
  const bundle = await esbuild.build({
    entryPoints: [path.join(__dirname, "../src/film-concat.ts")],
    bundle: true, platform: "node", format: "cjs", write: false,
    plugins: [{
      name: "test-dom",
      setup(build) {
        build.onResolve({ filter: /^\.\/editor$/ }, () => ({ path: "editor", namespace: "test" }));
        build.onResolve({ filter: /\.css$/ }, () => ({ path: "css", namespace: "test" }));
        build.onLoad({ filter: /^css$/, namespace: "test" }, () => ({ contents: "", loader: "js" }));
        build.onLoad({ filter: /^editor$/, namespace: "test" }, () => ({ contents: `
          export function el(tag, props = {}, ...children) {
            const node = { tag, ...props, children, isConnected: true, handlers: {}, attrs: {},
              classList: { toggle() {}, add() {} },
              setAttribute(k, v) { this.attrs[k] = v; },
              addEventListener(k, fn) { this.handlers[k] = fn; } };
            return node;
          }
        `, loader: "js" }));
      },
    }],
  });
  const loaded = new Module(path.join(__dirname, "film-concat-bundle.cjs"), module);
  loaded.filename = path.join(__dirname, "film-concat-bundle.cjs");
  loaded.paths = module.paths;
  loaded._compile(bundle.outputFiles[0].text, loaded.filename);
  return loaded.exports.filmConcatControls;
}

const film = (overrides = {}) => ({
  id: "test-project", status: "done", settings: { mode: "filming", film: { episodes: 2 }, clip: { count: 2 } },
  clips: [{ n: 1, status: "done", video: "/1.mp4" }, { n: 2, status: "done", video: "/2.mp4" }],
  ...overrides,
});
const reply = (status, progress = 0, message = "") => ({ ok: true, json: async () => ({ status, progress, message }) });

test("completed clips show real progress and download after server completion", async () => {
  const controller = await loadController();
  const scheduled = [];
  global.window = { setTimeout: (fn) => scheduled.push(fn) };
  const responses = [reply("idle"), reply("running", 0.25, "Menggabungkan"), reply("running", 0.75, "Menggabungkan"), reply("done")];
  const calls = [];
  global.fetch = async (url, options) => { calls.push([url, options]); return responses.shift(); };
  const [button, download, progress, note] = controller(film()).children;
  assert.equal(button.disabled, true);
  await scheduled.shift()();
  assert.equal(button.disabled, false);
  await button.handlers.click();
  assert.equal(button.disabled, true);
  assert.equal(progress.value, 0.25);
  assert.match(note.textContent, /25%/);
  await scheduled.shift()();
  assert.equal(progress.value, 0.75);
  await scheduled.shift()();
  assert.equal(download.hidden, false);
  assert.match(download.href, /test-project\/film-sequence\/download$/);
  assert.equal(progress.hidden, true);
  assert.equal(calls[1][1].method, "POST");
});

test("unfinished or failed clips prevent combining without a description", async () => {
  const controller = await loadController();
  const scheduled = [];
  global.window = { setTimeout: (fn) => scheduled.push(fn) };
  global.fetch = async () => reply("idle");
  const project = film({ clips: [{ n: 1, status: "done", video: "/1.mp4" }, { n: 2, status: "error" }] });
  const [button, download, , note] = controller(project).children;
  await scheduled.shift()();
  assert.equal(button.disabled, true);
  assert.equal(download.hidden, true);
  assert.equal(note.hidden, true);
  assert.equal(controller(film({ clips: [] })), null);
});

test("backend incompatibility is shown without offering a download", async () => {
  const controller = await loadController();
  const scheduled = [];
  global.window = { setTimeout: (fn) => scheduled.push(fn) };
  const responses = [reply("idle"), { ok: false, status: 409, json: async () => ({ detail: "Format clip tidak kompatibel" }) }];
  global.fetch = async () => responses.shift();
  const [button, download, , note] = controller(film()).children;
  await scheduled.shift()();
  await button.handlers.click();
  assert.equal(download.hidden, true);
  assert.equal(button.disabled, false);
  assert.match(note.textContent, /Format clip tidak kompatibel/);
});
