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
  dom.window.HTMLElement.prototype.scrollIntoView = function () { this.dataset.scrolled = "true"; };
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
    else if (url.endsWith("/film-sequence/cover")) data = options?.method === "POST"
      ? { status: "done", progress: 1, message: "Siap", has_applied: true, title: "Judul pilihan" }
      : { status: "idle", progress: 0, message: "", has_applied: false, title: "" };
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
  const sections = [...document.querySelectorAll("#editor-other-projects .editor-project")];
  assert.equal(sections.length, 2);
  assert.match(sections[0].querySelector("h2").textContent, /Film/);
  assert.match(sections[1].querySelector("h2").textContent, /Proyek lain/);
  assert.equal(sections[0].dataset.scrolled, "true");
  const cards = [...sections[0].querySelectorAll(".editor-clip")];
  assert.equal(cards.length, 3);
  assert.equal(sections[1].querySelectorAll(".editor-clip").length, 2);
  const grid = sections[0].querySelector(".editor-clips");
  Object.defineProperty(grid, "clientWidth", { value: 400 });
  Object.defineProperty(grid, "scrollWidth", { value: 1000 });
  cards[0].getBoundingClientRect = () => ({ width: 320 });
  grid.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(sections[0].querySelector(".editor-row").classList.contains("can-scroll-right"), true);
  let distance = 0;
  grid.scrollBy = (options) => { distance = options.left; };
  sections[0].querySelector('[aria-label="Geser video ke kanan"]').click();
  assert.equal(distance, 320);
  grid.scrollLeft = 100;
  grid.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(sections[0].querySelector(".editor-row").classList.contains("can-scroll-left"), true);
  assert.equal(cards[2].querySelector("video").getAttribute("src"), "/api/projects/test/film-sequence/video");
  assert.equal(cards[2].querySelector("a[download]").getAttribute("href"), "/api/projects/test/film-sequence/download");
  assert.equal(cards[2].querySelector('input[aria-label="Judul video gabungan"]').value, "Judul utuh");
  assert.doesNotMatch(cards[2].textContent, /Gabungan clip/);
  const titleInput = cards[2].querySelector('input[aria-label="Judul video gabungan"]');
  const apply = cards[2].querySelector('button[aria-label="Terapkan judul sebagai thumbnail"]');
  const download = cards[2].querySelector('a[download]');
  assert.equal(download.textContent, "download");
  assert.ok(titleInput.compareDocumentPosition(download) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.ok(download.compareDocumentPosition(apply) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  titleInput.value = "Judul pilihan";
  titleInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  apply.click();
  await new Promise((resolve) => setTimeout(resolve, 20));
  const coverCall = calls.find(([url, options]) => url === "/api/projects/test/film-sequence/cover" && options?.method === "POST");
  assert.equal(JSON.parse(coverCall[1].body).title, "Judul pilihan");
  assert.match(cards[2].querySelector("video").poster, /film-sequence\/cover\/image/);
  assert.equal(cards[2].querySelector('a[download="thumbnail-gabungan.jpg"]').hidden, false);
  assert.deepEqual([...cards[0].querySelectorAll('[role="tab"]')].map((b) => b.textContent), ["pendek", "sedang", "panjang"]);
  const panel = cards[0].querySelector(".posts");
  assert.match(panel.children[0].textContent, /deskripsi.*copy/);
  assert.equal(panel.children[1].className, "tabs");
  assert.equal(cards[0].querySelector("textarea").value, "Sedang");
  cards[0].querySelector('[data-length="pendek"]').click();
  assert.equal(cards[0].querySelector("textarea").value, "Pendek");
  assert.equal(cards[0].querySelector(".count"), null);
  assert.ok(calls.some(([url]) => url.endsWith("/dashboard-text")));
  const otherDescription = sections[1].querySelector("textarea");
  otherDescription.value = "Edit untuk proyek lain";
  otherDescription.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 750));
  const saveCall = calls.find(([url, options]) => url === "/api/projects/other/dashboard-text" && options?.method === "PUT");
  assert.ok(saveCall, "edit harus disimpan ke proyek yang benar");
  assert.equal(JSON.parse(saveCall[1].body).text, "Edit untuk proyek lain");
  dom.window.location.hash = "#/project/other";
  await new Promise((resolve) => setTimeout(resolve, 100));
  const rerendered = [...document.querySelectorAll("#editor-other-projects .editor-project")];
  assert.match(rerendered[0].querySelector("h2").textContent, /Film/);
  assert.match(rerendered[1].querySelector("h2").textContent, /Proyek lain/);
  assert.equal(rerendered[1].dataset.scrolled, "true");
  const otherCards = [...rerendered[1].querySelectorAll(".editor-clip")];
  assert.equal(otherCards.length, 2);
  assert.match(otherCards[0].textContent, /Hasil proyek lain/);
  assert.match(rerendered[0].textContent, /Klip satu/);
  dom.window.close();
});

test("combined video can download or apply an edited title before generated text loads", async () => {
  const dom = new JSDOM(`<!doctype html><body><div id="preview-modal" hidden></div><video id="modal-video"></video>
    <p id="modal-title"></p><a id="modal-download"></a><button id="modal-edit"></button><button id="modal-close"></button>
    <section id="editor" hidden><button id="editor-back"></button><img id="editor-thumb"><h2 id="editor-title"></h2>
    <p id="editor-meta"></p><p id="editor-hint"></p><div id="editor-scroll-controls"></div>
    <div id="editor-primary-row"><ol id="editor-clips"></ol></div><div id="editor-other-projects"></div></section></body>`,
    { url: "http://localhost:4747/#/project/delayed" });
  Object.assign(global, { window: dom.window, document: dom.window.document, location: dom.window.location, history: dom.window.history });
  const project = { id: "delayed", title: "Film", url: "", thumbnail: "", status: "done",
    settings: { mode: "filming", film: { episodes: 1 }, clip: { ratio: "9:16", fit: "crop" } },
    clips: [{ n: 1, title: "Bagian", status: "done", video: "/1.mp4", start: 0, end: 1 }] };
  let resolveText;
  const delayed = new Promise((resolve) => { resolveText = resolve; });
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith("/dashboard-text") && options?.method !== "PUT") return delayed;
    let data = project;
    if (url === "/api/projects") data = [project];
    else if (url.endsWith("/film-sequence")) data = { status: "done", progress: 1 };
    else if (url.endsWith("/cover")) data = options?.method === "POST"
      ? { status: "done", has_applied: true, title: "Judul saya", progress: 1 }
      : { status: "idle", has_applied: false, progress: 0 };
    return { ok: true, json: async () => data };
  };
  const result = await build({ entryPoints: ["src/editor.ts"], bundle: true, write: false, platform: "node", format: "cjs" });
  const compiled = new Module(path.join(process.cwd(), "tests", "editor-delayed.cjs"), module);
  compiled.filename = path.join(process.cwd(), "tests", "editor-delayed.cjs");
  compiled.paths = Module._nodeModulePaths(process.cwd());
  compiled._compile(result.outputFiles[0].text, compiled.filename);
  await new Promise((resolve) => setTimeout(resolve, 40));
  const card = [...document.querySelectorAll("#editor-other-projects .editor-clip")][1];
  assert.equal(card.querySelector('a[download]').textContent, "download");
  const input = card.querySelector('input[aria-label="Judul video gabungan"]');
  input.value = "Judul saya";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  card.querySelector('button[aria-label="Terapkan judul sebagai thumbnail"]').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(JSON.parse(calls.find(([url, opts]) => url.endsWith("/cover") && opts?.method === "POST")[1].body).title, "Judul saya");
  const generated = { pendek: "p", sedang: "s", panjang: "l", title: "Judul otomatis" };
  resolveText({ ok: true, json: async () => ({ "1": generated, sequence: generated }) });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(input.value, "Judul saya");
  dom.window.close();
});
