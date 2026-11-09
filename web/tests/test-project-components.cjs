const test = require("node:test");
const assert = require("node:assert/strict");
const { build } = require("esbuild");
const { JSDOM } = require("jsdom");
const Module = require("node:module");
const path = require("node:path");

test("React project islands preserve settings, preview, retry and cleanup", async () => {
  const dom = new JSDOM("<!doctype html><body></body>", { url: "http://localhost:4747" });
  global.window = dom.window;
  global.document = dom.window.document;
  global.navigator = dom.window.navigator;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const { act } = require("react");
  const result = await build({
    entryPoints: ["src/project-components.tsx"], bundle: true, write: false, platform: "node", format: "cjs",
    external: ["react", "react/*", "react-dom", "react-dom/*"],
    plugins: [{ name: "editor-boundary", setup(builder) {
      builder.onResolve({ filter: /^\.\/editor$/ }, () => ({ path: "editor", namespace: "test" }));
      builder.onLoad({ filter: /.*/, namespace: "test" }, () => ({ contents: 'exports.clipLayout = () => "9:16 · no crop · blur"', loader: "js" }));
    } }],
  });
  const compiled = new Module(path.join(process.cwd(), "tests", "react-bundle.cjs"), module);
  compiled.filename = path.join(process.cwd(), "tests", "react-bundle.cjs");
  compiled.paths = Module._nodeModulePaths(process.cwd());
  compiled._compile(result.outputFiles[0].text, compiled.filename);
  const { mountProjectSettings, mountClipResults, disposeProjectIslands } = compiled.exports;
  const project = {
    id: "one", status: "done", has_logo: true, transcript_origin: "youtube_auto",
    settings: { mode: "filming", clip: { count: 2, max_duration: 60 },
      caption: { font: "font", animation: "fade", color: "white", case: "sentence" },
      thumb: { source: "auto", title_style: "plain", voice: null },
      brand: { position: "bc", text: "CLIPY", opacity: 60 } },
    clips: [{ n: 1, title: "Opening", status: "done", video: "/clip.mp4" },
      { n: 2, title: "Ending", status: "error", error: "gagal render" }],
  };
  const card = document.createElement("article");
  let preview, retried;
  await act(async () => {
    card.append(mountProjectSettings({ project, label: (_, key) => key ?? "", colors: { white: "#fff" }, positions: { bc: "bottom" } }));
    card.append(mountClipResults({ project, onPreview: (p, c) => { preview = [p.id, c.n]; },
      onRetry: async (id, n) => { retried = [id, n]; throw new Error("retry gagal"); }, onRefresh: () => {} }));
    document.body.append(card);
  });
  assert.match(card.querySelector("dl").textContent, /YouTube transkrip/);
  assert.match(card.querySelector("dl").textContent, /thumbnail/);
  assert.match(card.querySelector("dl").textContent, /2 baris/);
  assert.doesNotMatch(card.querySelector("dl").textContent, /info transkrip/);
  assert.match(card.querySelector("dl").textContent, /CLIPY.*60%.*bottom/);
  assert.equal(card.querySelectorAll("ol.clips > li").length, 2);
  await act(async () => card.querySelector(".link-title").click());
  assert.deepEqual(preview, ["one", 1]);
  await act(async () => card.querySelector("button.warn").click());
  assert.deepEqual(retried, ["one", 2]);
  assert.match(card.querySelector(".clip-error").textContent, /retry gagal/);
  await act(async () => disposeProjectIslands(card));
  assert.equal(card.querySelector("dl").children.length, 0);
  assert.equal(card.querySelector("ol").children.length, 0);
  dom.window.close();
});
