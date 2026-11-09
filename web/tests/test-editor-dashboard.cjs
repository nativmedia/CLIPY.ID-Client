const test = require("node:test");
const assert = require("node:assert/strict");
const { build } = require("esbuild");
const { JSDOM } = require("jsdom");
const Module = require("node:module");
const path = require("node:path");

test("dashboard displays clip descriptions and the combined video for one project", async () => {
  const dom = new JSDOM(`<!doctype html><body>
    <div id="preview-modal" hidden></div><video id="modal-video"></video><p id="modal-title"></p>
    <a id="modal-download"></a><button id="modal-edit"></button><button id="modal-close"></button>
    <section id="editor" hidden><button id="editor-back"></button><img id="editor-thumb">
    <h2 id="editor-title"></h2><p id="editor-meta"></p><p id="editor-hint"></p><div id="editor-scroll-controls"></div>
    <div id="editor-primary-row" class="editor-row"><ol id="editor-clips"></ol></div><div id="editor-other-projects"></div></section>
    </body>`, { url: "http://localhost:4747/#/project/test" });
  Object.assign(global, { window: dom.window, document: dom.window.document, location: dom.window.location,
                          history: dom.window.history });
  const project = { id: "test", title: "Film", url: "", thumbnail: "", status: "done",
    settings: { mode: "filming", film: { episodes: 2 }, clip: { ratio: "9:16", fit: "crop" } },
    clips: [{ n: 1, title: "Klip satu", status: "done", video: "/1.mp4", start: 0, end: 1 },
            { n: 2, title: "Klip dua", status: "done", video: "/2.mp4", start: 1, end: 2 }] };
  const descriptions = { pendek: "Pendek", sedang: "Sedang", panjang: "Panjang" };
  const other = { id: "other", title: "Proyek lain", url: "", thumbnail: "", status: "done",
    settings: { mode: "filming", film: { episodes: 1 }, clip: { ratio: "9:16", fit: "crop" } },
    clips: [{ n: 1, title: "Hasil proyek lain", status: "done", video: "/other.mp4", start: 0, end: 1 }] };
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push([url, options]);
    let data;
    if (url.endsWith("/dashboard-text")) data = { "1": { ...descriptions }, "2": { ...descriptions }, sequence: { ...descriptions, title: "Judul utuh" } };
    else if (url.endsWith("/film-sequence")) data = { status: "done", progress: 1, message: "Siap" };
    else if (url === "/api/projects") data = [project, other];
    else data = url.endsWith("/other") ? other : project;
    return { ok: true, json: async () => data };
  };
  const result = await build({ entryPoints: ["src/editor.ts"], bundle: true, write: false, platform: "node", format: "cjs" });
  const compiled = new Module(path.join(process.cwd(), "tests", "editor-bundle.cjs"), module);
  compiled.filename = path.join(process.cwd(), "tests", "editor-bundle.cjs");
  compiled.paths = Module._nodeModulePaths(process.cwd());
  compiled._compile(result.outputFiles[0].text, compiled.filename);
  await new Promise((resolve) => setTimeout(resolve, 100));
  const cards = [...document.querySelectorAll("#editor-clips .editor-clip")];
  assert.equal(cards.length, 3);
  assert.equal(document.querySelectorAll(".editor-other-project .editor-clip").length, 2);
  assert.match(document.querySelector(".editor-other-project h2").textContent, /Proyek lain/);
  const grid = document.querySelector("#editor-clips");
  Object.defineProperty(grid, "clientWidth", { value: 400 });
  Object.defineProperty(grid, "scrollWidth", { value: 1000 });
  grid.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(document.querySelector("#editor-primary-row").classList.contains("can-scroll-right"), true);
  let distance = 0;
  grid.scrollBy = (options) => { distance = options.left; };
  document.querySelector('#editor-scroll-controls [aria-label="Geser video ke kanan"]').click();
  assert.equal(distance, 320);
  grid.scrollLeft = 100;
  grid.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(document.querySelector("#editor-primary-row").classList.contains("can-scroll-left"), true);
  assert.equal(cards[2].querySelector("video").getAttribute("src"), "/api/projects/test/film-sequence/video");
  assert.equal(cards[2].querySelector("a[download]").getAttribute("href"), "/api/projects/test/film-sequence/download");
  assert.equal(cards[2].querySelector('input[aria-label="Judul video gabungan"]').value, "Judul utuh");
  assert.deepEqual([...cards[0].querySelectorAll('[role="tab"]')].map((b) => b.textContent), ["pendek", "sedang", "panjang"]);
  assert.equal(cards[0].querySelector("textarea").value, "Sedang");
  cards[0].querySelector('[data-length="pendek"]').click();
  assert.equal(cards[0].querySelector("textarea").value, "Pendek");
  assert.equal(cards[0].querySelector(".count"), null);
  assert.ok(calls.some(([url]) => url.endsWith("/dashboard-text")));
  const otherDescription = document.querySelector('.editor-other-project textarea');
  otherDescription.value = "Edit untuk proyek lain";
  otherDescription.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 750));
  const saveCall = calls.find(([url, options]) => url === "/api/projects/other/dashboard-text" && options?.method === "PUT");
  assert.ok(saveCall, "edit harus disimpan ke proyek yang benar");
  assert.equal(JSON.parse(saveCall[1].body).text, "Edit untuk proyek lain");
  dom.window.location.hash = "#/project/other";
  await new Promise((resolve) => setTimeout(resolve, 100));
  const otherCards = [...document.querySelectorAll("#editor-clips .editor-clip")];
  assert.equal(otherCards.length, 2);
  assert.match(otherCards[0].textContent, /Hasil proyek lain/);
  assert.doesNotMatch(document.querySelector("#editor-clips").textContent, /Klip satu/);
  dom.window.close();
});
