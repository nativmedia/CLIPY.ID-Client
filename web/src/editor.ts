/**
 * Result views: the clip preview pop-up and the project editor page, where each finished
 * clip sits next to editable short, medium and long descriptions.
 */

export type Clip = {
  n: number;
  title: string;
  reason?: string;
  status: string;
  error?: string;
  note?: string;
  video?: string;
  thumb?: string;
  srt?: string;
  start: number;
  end: number;
};

export type Project = {
  id: string;
  url: string;
  title: string;
  thumbnail: string;
  status: string;
  stage: string;
  progress: number;
  error: string | null;
  transcript_origin?: string | null;
  transcript_note?: string | null;
  transcription_backend?: string | null;
  transcription_note?: string | null;
  settings: {
    mode?: string;
    film?: { episodes: number; max_duration: number; style: string; voice: string; source_ratio?: string; background?: string; additional_prompt?: string } | null;
    mood?: string;
    music_start?: number;
    music_length?: number | null;
    clip: { ratio: string; fit: string; count: number; max_duration: number; dubbing_speed?: number; clip_ratio?: string | null; background?: string | null };
    caption: { font: string; animation: string; color: string; case: string; position?: number | null; display?: string | null; text_style?: string; max_lines?: number };
    thumb: { source: string; title_style: string; text?: string; text_mode?: "auto" | "manual" | "none"; numbered?: boolean; sequence?: string | null; position?: string | null; voice: string | null };
    brand?: { position: string; text?: string; style?: string; opacity?: number };
  };
  has_logo?: boolean;
  has_export_dir?: boolean;
  export_ready?: boolean;
  export_status?: "pending" | "copying" | "done" | "error" | null;
  export_error?: string | null;
  music?: string | null;
  clips: Clip[];
};

export function clipLayout(settings: Project["settings"]): string {
  const film = settings.mode === "filming" ? settings.film : null;
  const crop = film?.source_ratio ?? settings.clip.clip_ratio;
  const background = film?.background ?? settings.clip.background;
  if (crop != null) return `${settings.clip.ratio} · ${crop === "auto" ? "ukuran asli" : crop === "none" ? "no crop" : crop} · ${background ?? "blur"}`;
  return `${settings.clip.ratio} ${settings.clip.fit === "crop" ? "crop" : "no crop"}`;
}

type LengthName = "pendek" | "sedang" | "panjang";
type DashboardText = { title?: string; pendek: string; sedang: string; panjang: string };
type DashboardTexts = Record<string, DashboardText>;

/** Small DOM builder; text always goes in as text, never as HTML. */
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {}, ...children: (Node | string | null | false | undefined)[]) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter((c): c is Node | string => !!c));
  return node;
}

const $ = <T extends HTMLElement>(sel: string) => document.querySelector<T>(sel)!;

export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Ask the server to make a failed clip again. */
export async function retryClip(projectId: string, n: number) {
  const res = await fetch(`/api/projects/${projectId}/clips/${n}/retry`, { method: "POST" });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `status ${res.status}`);
}

// ---------- preview pop-up ----------

const modal = $<HTMLDivElement>("#preview-modal");
const modalVideo = $<HTMLVideoElement>("#modal-video");
const modalTitle = $<HTMLParagraphElement>("#modal-title");
const modalEdit = $<HTMLButtonElement>("#modal-edit");
let modalProject = "";

export function openPreview(project: Project, clip: Clip) {
  if (!clip.video) return;
  modalProject = project.id;
  modalVideo.src = clip.video;
  modalVideo.poster = clip.thumb ?? "";
  modalVideo.style.aspectRatio = project.settings.clip.ratio.replace(":", " / ");
  modalTitle.textContent = clip.title;
  modal.hidden = false;
  document.body.classList.add("locked");
  modalVideo.play().catch(() => {});
  $<HTMLButtonElement>("#modal-close").focus();
}

function closePreview() {
  if (modal.hidden) return;
  modalVideo.pause();
  modalVideo.removeAttribute("src");
  modalVideo.load();
  modal.hidden = true;
  if (editor.hidden) document.body.classList.remove("locked");
}

$<HTMLButtonElement>("#modal-close").addEventListener("click", closePreview);
modal.addEventListener("click", (e) => {
  if (e.target === modal) closePreview();
});
modalEdit.addEventListener("click", () => {
  const id = modalProject;
  closePreview();
  openEditor(id);
});

// ---------- editor page ----------

const editor = $<HTMLElement>("#editor");
const editorClips = $<HTMLOListElement>("#editor-clips");
const otherProjects = $<HTMLDivElement>("#editor-other-projects");
const scrollControls = $<HTMLDivElement>("#editor-scroll-controls");
const editorHint = $<HTMLParagraphElement>("#editor-hint");
let editing = "";
const pendingSaves = new Map<string, number>();
const saveQueues = new Map<string, Promise<void>>();

export function openEditor(id: string) {
  location.hash = `#/project/${id}`;
}

function closeEditor() {
  if (location.hash.startsWith("#/project/")) history.pushState("", "", location.pathname + location.search);
  route();
}

$<HTMLButtonElement>("#editor-back").addEventListener("click", closeEditor);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!modal.hidden) closePreview();
  else if (!editor.hidden) closeEditor();
});

function queueSave(projectId: string, n: string, field: string, text: string, status: HTMLElement) {
  const key = `${projectId}:${n}:${field}`;
  status.textContent = "saving…";
  clearTimeout(pendingSaves.get(key));
  pendingSaves.set(key, window.setTimeout(() => {
    pendingSaves.delete(key);
    const next = (saveQueues.get(key) ?? Promise.resolve()).then(async () => {
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/dashboard-text`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ item: n, field, text }),
        });
        if (!res.ok) throw new Error(String(res.status));
        if (saveQueues.get(key) === next && !pendingSaves.has(key)) status.textContent = "saved";
      } catch {
        if (saveQueues.get(key) === next && !pendingSaves.has(key)) status.textContent = "couldn't save";
      }
    });
    saveQueues.set(key, next);
    void next.then(() => { if (saveQueues.get(key) === next) saveQueues.delete(key); });
  }, 700));
}

async function copy(text: string, button: HTMLButtonElement) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "copied";
  } catch {
    button.textContent = "copy failed";
  }
  setTimeout(() => (button.textContent = "copy"), 1400);
}

/** Three independently editable description lengths, shared by clips and sequence. */
function postsPanel(projectId: string, n: string, values: DashboardText): HTMLElement {
  const box = el("div", { className: "posts" });
  const tabs = el("div", { className: "tabs" });
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Panjang deskripsi");
  const body = el("div", { className: "post-fields" });
  const status = el("span", { className: "save-status" });
  status.setAttribute("role", "status");
  const copyButton = el("button", { type: "button", className: "text-button", textContent: "copy" });
  let currentArea: HTMLTextAreaElement | null = null;
  copyButton.addEventListener("click", () => { if (currentArea) void copy(currentArea.value, copyButton); });
  const show = (length: LengthName) => {
    tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.length === length)));
    const area = el("textarea", {
      value: values[length] ?? "", maxLength: { pendek: 70, sedang: 250, panjang: 600 }[length], spellcheck: true, rows: length === "pendek" ? 3 : 5,
    });
    area.setAttribute("aria-label", `Deskripsi ${length}`);
    currentArea = area;
    area.addEventListener("input", () => {
      values[length] = area.value;
      queueSave(projectId, n, length, area.value, status);
    });
    body.replaceChildren(el("div", { className: "post-field" }, area));
  };
  for (const length of ["pendek", "sedang", "panjang"] as const) {
    const b = el("button", { type: "button", textContent: length });
    b.dataset.length = length;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => show(length));
    tabs.append(b);
  }
  box.append(el("div", { className: "field-head" }, el("span", { textContent: "deskripsi" }), copyButton), tabs, body, status);
  show("sedang");
  return box;
}

function textPlaceholder(id: string): HTMLElement {
  const slot = el("div");
  slot.dataset.textId = id;
  slot.append(el("p", { className: "hint", textContent: "Menyiapkan deskripsi…" }));
  return slot;
}

function clipCard(p: Project, c: Clip): HTMLLIElement {
  const ratio = p.settings.clip.ratio.replace(":", " / ");
  let media: HTMLElement;
  if (c.video) {
    const v = el("video", { src: c.video, poster: c.thumb ?? "", controls: true, preload: "metadata", playsInline: true });
    v.style.aspectRatio = ratio;
    media = v;
  } else {
    const empty = el("div", { className: "video-empty", textContent: c.status === "error" ? "failed" : c.status });
    empty.style.aspectRatio = ratio;
    media = empty;
  }

  const actions = el("div", { className: "clip-actions" });
  if (c.status === "error") {
    const retry = el("button", { type: "button", className: "text-button warn", textContent: "retry" });
    const running = !["done", "error", "cancelled"].includes(p.status);
    retry.disabled = running;
    if (running) retry.textContent = "tunggu proses selesai";
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      retry.textContent = "retrying…";
      try {
        await retryClip(p.id, c.n);
        followUntilDone(p.id);
      } catch (err) {
        retry.textContent = `retry failed: ${(err as Error).message}`;
        retry.disabled = false;
      }
    });
    actions.append(retry);
  }


  return el(
    "li",
    { className: "editor-clip" },
    el("div", { className: "clip-media" }, media),
    el(
      "div",
      { className: "clip-body" },
      el("p", { className: "kicker" }, el("span", { textContent: `clip ${String(c.n).padStart(2, "0")} · ${clock(c.start)}–${clock(c.end)}` })),
      el("h3", { textContent: c.title }),
      c.reason ? el("p", { className: "reason", textContent: c.reason }) : null,
      c.error ? el("p", { className: "hint error", textContent: c.error }) : null,
      c.note ? el("p", { className: "hint", textContent: c.note }) : null,
      actions,
      c.video ? textPlaceholder(String(c.n)) : null,
    ),
  );
}

function resultStatus(p: Project): HTMLElement {
  const complete = p.status === "done" && p.export_status !== "copying";
  const state = el("button", {
    type: "button",
    disabled: true,
    className: `result-state ${complete ? "is-complete" : "is-processing"}`,
    title: complete ? "Proses selesai" : "Sedang memproses",
    ariaLabel: complete ? "Proses selesai" : "Sedang memproses",
  });
  state.innerHTML = complete
    ? '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 3.5 3.5L16 5"/></svg>'
    : '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/></svg>';
  return state;
}

function titleActions(p: Project): HTMLElement[] {
  const actions: HTMLElement[] = [resultStatus(p)];
  if (p.has_export_dir) {
    const open = el("button", { type: "button", className: "text-button result-open", textContent: "Buka" });
    open.disabled = !p.export_ready;
    open.title = p.export_ready ? "Buka folder hasil" : "Folder hasil disiapkan setelah proses selesai";
    open.addEventListener("click", async () => {
      open.disabled = true;
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(p.id)}/output/open`, { method: "POST" });
        if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `status ${res.status}`);
      } catch (err) {
        open.title = (err as Error).message;
      } finally {
        open.disabled = !p.export_ready;
      }
    });
    actions.push(open);
  }
  return actions;
}

async function loadDashboardTexts(id: string, scope: HTMLElement, routeId: string) {
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(id)}/dashboard-text`);
    const data: DashboardTexts & { detail?: string } = await response.json();
    if (!response.ok) throw new Error(data.detail ?? `status ${response.status}`);
    if (editing !== routeId || editor.hidden || !scope.isConnected) return;
    scope.querySelectorAll<HTMLElement>("[data-text-id]").forEach((slot) => {
      const textId = slot.dataset.textId!;
      if (data[textId] && slot.dataset.loaded !== "true") {
        if (textId === "sequence") {
          const input = slot.closest(".clip-body")?.querySelector<HTMLInputElement>("[data-sequence-title]");
          if (input && input.dataset.edited !== "true") {
            input.value = data[textId].title ?? "";
            input.dispatchEvent(new window.Event("sequence-title-ready"));
          }
        }
        slot.replaceChildren(postsPanel(id, textId, data[textId]));
        slot.dataset.loaded = "true";
      }
    });
  } catch (error) {
    if (editing !== routeId || editor.hidden || !scope.isConnected) return;
    scope.querySelectorAll<HTMLElement>("[data-text-id]:not([data-loaded])").forEach((slot) => {
      const retry = el("button", { type: "button", className: "text-button", textContent: "coba lagi" });
      retry.addEventListener("click", () => { retry.disabled = true; void loadDashboardTexts(id, scope, routeId); });
      slot.replaceChildren(el("p", { className: "hint error", textContent: `Deskripsi belum tersedia: ${(error as Error).message}` }), retry);
    });
  }
}

type SequenceState = { status: "idle" | "running" | "done" | "error"; progress: number; message: string };
type CoverState = { status: "idle" | "running" | "done" | "error"; progress: number; message: string; has_applied: boolean; title: string };
const sequenceActivators = new WeakMap<Element, () => void>();
const projectObservers = new Set<IntersectionObserver>();
const rowObservers = new Set<ResizeObserver>();

function scrollButtons(grid: HTMLOListElement, row: HTMLElement) {
  grid.tabIndex = 0;
  grid.setAttribute("aria-label", "Daftar video proyek, dapat digeser ke samping");
  const icon = (direction: "left" | "right") => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", direction === "left" ? "M14.5 4 6.5 12l8 8" : "m9.5 4 8 8-8 8");
    svg.append(path);
    return svg;
  };
  const left = el("button", { type: "button", className: "editor-scroll-button", title: "Geser video ke kiri" }, icon("left"));
  const right = el("button", { type: "button", className: "editor-scroll-button", title: "Geser video ke kanan" }, icon("right"));
  left.setAttribute("aria-label", "Geser video ke kiri");
  right.setAttribute("aria-label", "Geser video ke kanan");
  const update = () => {
    const max = Math.max(0, grid.scrollWidth - grid.clientWidth);
    row.classList.toggle("can-scroll-left", grid.scrollLeft > 1);
    row.classList.toggle("can-scroll-right", grid.scrollLeft < max - 1);
    left.disabled = grid.scrollLeft <= 1;
    right.disabled = grid.scrollLeft >= max - 1;
  };
  grid.onscroll = update;
  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(update);
    rowObservers.add(observer);
    observer.observe(grid);
  }
  window.setTimeout(update, 0);
  const clipStep = () => {
    const card = grid.querySelector<HTMLElement>(".editor-clip");
    const gap = Number.parseFloat(getComputedStyle(grid).columnGap) || 0;
    return (card?.getBoundingClientRect().width || grid.clientWidth) + gap;
  };
  left.addEventListener("click", () => grid.scrollBy({ left: -clipStep(), behavior: "smooth" }));
  right.addEventListener("click", () => grid.scrollBy({ left: clipStep(), behavior: "smooth" }));
  return el("div", { className: "editor-scroll-controls" }, left, right);
}

/** Each project's combined video stays inside its own project grid. */
function sequenceCard(p: Project, scope: HTMLElement, routeId: string): HTMLLIElement {
  const endpoint = `/api/projects/${encodeURIComponent(p.id)}/film-sequence`;
  const media = el("div", { className: "clip-media" });
  const placeholder = el("div", { className: "video-empty", textContent: "Memeriksa video gabungan…" });
  placeholder.style.aspectRatio = p.settings.clip.ratio.replace(":", " / ");
  media.append(placeholder);
  const body = el("div", { className: "clip-body" });
  const actions = el("div", { className: "clip-actions" });
  const note = el("p", { className: "hint" });
  note.setAttribute("role", "status");
  const button = el("button", { type: "button", className: "text-button", textContent: `Gabungkan Clip 1-${p.settings.film?.episodes ?? p.clips.length}` });
  const complete = p.status === "done" && p.clips.length === p.settings.film?.episodes && p.clips.every((c) => c.status === "done" && !!c.video);
  button.disabled = !complete;
  actions.append(button);
  body.append(el("p", { className: "kicker" }, el("span", { textContent: "video gabungan" })), actions, note);
  const card = el("li", { className: "editor-clip" }, media, body);
  let timer = 0;
  const show = (state: SequenceState) => {
    if (!card.isConnected || editing !== routeId) return;
    clearTimeout(timer);
    if (state.status === "done") {
      const video = el("video", { src: `${endpoint}/video`, controls: true, preload: "metadata", playsInline: true });
      video.style.aspectRatio = p.settings.clip.ratio.replace(":", " / ");
      media.replaceChildren(video);
      const title = el("input", { maxLength: 120, placeholder: "Judul thumbnail" });
      title.dataset.sequenceTitle = "";
      title.setAttribute("aria-label", "Judul video gabungan");
      const titleStatus = el("span", { className: "save-status" });
      titleStatus.setAttribute("role", "status");
      const titleCopy = el("button", { type: "button", className: "text-button", textContent: "copy" });
      titleCopy.addEventListener("click", () => { void copy(title.value, titleCopy); });
      const titleBox = el("div", { className: "post-field sequence-title" },
        el("div", { className: "field-head" }, el("span", { textContent: "judul" }), titleCopy), title, titleStatus);
      const download = el("a", { className: "text-button", href: `${endpoint}/download`, download: "", textContent: "download" });
      const apply = el("button", { type: "button", className: "text-button", textContent: "apply", disabled: true });
      apply.setAttribute("aria-label", "Terapkan judul sebagai thumbnail");
      const coverNote = el("p", { className: "hint sequence-cover-status" });
      coverNote.setAttribute("role", "status");
      const coverLink = el("a", { className: "text-button", href: `${endpoint}/cover/image`, download: "thumbnail-gabungan.jpg", textContent: "download thumbnail", hidden: true });
      actions.classList.add("sequence-actions");
      actions.replaceChildren(download, apply);
      body.insertBefore(titleBox, actions);
      body.insertBefore(coverNote, note);
      body.insertBefore(coverLink, note);
      note.textContent = "";
      let coverTimer = 0;
      let covering = false;
      title.addEventListener("sequence-title-ready", () => { apply.disabled = covering || !title.value.trim(); });
      title.addEventListener("input", () => {
        title.dataset.edited = "true";
        apply.disabled = covering || !title.value.trim();
        queueSave(p.id, "sequence", "title", title.value, titleStatus);
      });
      const showCover = (cover: CoverState) => {
        if (!card.isConnected || editing !== routeId) return;
        clearTimeout(coverTimer);
        covering = cover.status === "running";
        apply.disabled = covering || !title.value.trim();
        apply.textContent = covering ? "menerapkan…" : "apply";
        coverNote.classList.toggle("error", cover.status === "error");
        coverNote.textContent = covering ? `${cover.message || "Menerapkan thumbnail"} · ${Math.round(cover.progress * 100)}%`
          : cover.status === "error" ? cover.message : cover.has_applied ? `Thumbnail diterapkan: ${cover.title}` : "";
        coverLink.hidden = !cover.has_applied;
        if (cover.has_applied) {
          const imageUrl = `${endpoint}/cover/image?v=${Date.now()}`;
          video.poster = imageUrl;
          coverLink.href = imageUrl;
        }
        if (covering) coverTimer = window.setTimeout(checkCover, 1000);
      };
      const checkCover = async () => {
        if (!card.isConnected || editing !== routeId) return;
        try {
          const response = await fetch(`${endpoint}/cover`, { cache: "no-store" });
          if (!response.ok) throw new Error(`status ${response.status}`);
          showCover(await response.json());
        } catch {
          if (card.isConnected) { coverNote.textContent = "Status thumbnail tidak dapat diperiksa."; coverNote.classList.add("error"); }
        }
      };
      apply.addEventListener("click", async () => {
        if (!title.value.trim()) return;
        covering = true;
        apply.disabled = true;
        coverNote.textContent = "Menyiapkan thumbnail…";
        try {
          const response = await fetch(`${endpoint}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.value }) });
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail ?? `status ${response.status}`);
          showCover(data);
        } catch (error) {
          covering = false;
          apply.disabled = !title.value.trim();
          coverNote.textContent = (error as Error).message;
          coverNote.classList.add("error");
        }
      });
      void checkCover();
      if (!body.querySelector("[data-text-id]")) {
        body.append(textPlaceholder("sequence"));
        void loadDashboardTexts(p.id, scope, routeId);
      }
      return;
    }
    const running = state.status === "running";
    button.disabled = !complete || running;
    button.textContent = running ? "Menggabungkan…" : `Gabungkan Clip 1-${p.settings.film?.episodes ?? p.clips.length}`;
    placeholder.textContent = running ? `Menggabungkan · ${Math.round(state.progress * 100)}%` : "Video gabungan belum tersedia";
    note.textContent = state.status === "error" ? state.message : !complete ? "Selesaikan atau ulangi semua clip terlebih dahulu." : running ? state.message : "";
    note.classList.toggle("error", state.status === "error");
    if (running) timer = window.setTimeout(check, 1000);
  };
  const check = async () => {
    if (!card.isConnected || editing !== routeId) return;
    try {
      const response = await fetch(endpoint, { cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      show(await response.json());
    } catch {
      if (card.isConnected) { note.textContent = "Status gabungan tidak dapat diperiksa. Coba buka dashboard kembali."; note.classList.add("error"); }
    }
  };
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch(endpoint, { method: "POST" });
      const state = await response.json();
      if (!response.ok) throw new Error(state.detail ?? `status ${response.status}`);
      show(state);
    } catch (error) {
      button.disabled = !complete;
      note.textContent = (error as Error).message;
      note.classList.add("error");
    }
  });
  sequenceActivators.set(card, () => { void check(); });
  if (scope === editorClips) window.setTimeout(check, 0);
  return card;
}

let follow = 0;
/** After a retry, re-render the editor until the clip settles. */
function followUntilDone(id: string, routeId = editing) {
  clearTimeout(follow);
  follow = window.setTimeout(async () => {
    if (editing !== routeId) return;
    try {
      const response = await fetch(`/api/projects/${id}`);
      if (!response.ok) throw new Error(`status ${response.status}`);
      const p: Project = await response.json();
      if (["done", "error", "cancelled"].includes(p.status)) void renderEditor(routeId);
      else followUntilDone(id, routeId);
    } catch {
      if (editing === routeId) followUntilDone(id, routeId);
    }
  }, 2000);
}

async function renderEditor(id: string) {
  editing = id;
  editorHint.textContent = "";
  editorHint.classList.remove("error");
  const res = await fetch(`/api/projects/${id}`);
  if (editing !== id || editor.hidden) return;
  if (!res.ok) {
    editorClips.replaceChildren();
    editorHint.textContent = "project not found";
    editorHint.classList.add("error");
    return;
  }
  const p: Project = await res.json();
  $<HTMLImageElement>("#editor-thumb").src = p.thumbnail;
  $<HTMLElement>("#editor-title").textContent = p.title || p.url;
  $<HTMLElement>("#editor-title-actions").replaceChildren(...titleActions(p));
  const s = p.settings;
  $<HTMLElement>("#editor-meta").replaceChildren(
    `${clipLayout(s)} · ${p.clips.filter((c) => c.status === "done").length}/${p.clips.length} clips · `,
    p.url ? el("a", { href: p.url, target: "_blank", rel: "noopener", textContent: "source video" }) : "uploaded video",
  );

  projectObservers.forEach((observer) => observer.disconnect());
  projectObservers.clear();
  rowObservers.forEach((observer) => observer.disconnect());
  rowObservers.clear();
  otherProjects.replaceChildren();
  editorClips.scrollLeft = 0;
  scrollControls.replaceChildren(scrollButtons(editorClips, $<HTMLElement>("#editor-primary-row")));
  editorClips.replaceChildren(...p.clips.map((c) => clipCard(p, c)),
    ...(p.settings.mode === "filming" && p.clips.length ? [sequenceCard(p, editorClips, id)] : []));
  if (p.clips.some((c) => c.video)) void loadDashboardTexts(id, editorClips, id);
  if (!["done", "error", "cancelled"].includes(p.status)) followUntilDone(id);
  try {
    const listResponse = await fetch("/api/projects");
    if (!listResponse.ok) return;
    const others: Project[] = await listResponse.json();
    if (editing !== id || editor.hidden) return;
    for (const other of others) {
      if (other.id === id || !other.clips.length) continue;
      const grid = el("ol", { className: "editor-clips" });
      const row = el("div", { className: "editor-row" }, grid);
      const section = el("section", { className: "editor-other-project" },
        el("div", { className: "editor-intro" },
          el("img", { className: "editor-thumb", src: other.thumbnail, alt: "" }),
          el("div", {}, el("div", { className: "editor-title-row" }, el("h2", { textContent: other.title || other.url }), ...titleActions(other)),
             el("p", { className: "editor-meta", textContent: `${clipLayout(other.settings)} · ${other.clips.filter((c) => c.status === "done").length}/${other.clips.length} clips` }))),
        scrollButtons(grid, row), row);
      otherProjects.append(section);
      const combined = other.settings.mode === "filming" ? sequenceCard(other, grid, id) : null;
      grid.replaceChildren(...other.clips.map((c) => clipCard(other, c)),
        ...(combined ? [combined] : []));
      // Don't spend API calls generating descriptions for off-screen projects.
      const load = () => {
        if (combined) sequenceActivators.get(combined)?.();
        if (other.clips.some((c) => c.video)) void loadDashboardTexts(other.id, grid, id);
      };
      if ("IntersectionObserver" in window) {
        const observer = new IntersectionObserver((entries) => {
          if (entries.some((entry) => entry.isIntersecting)) { observer.disconnect(); projectObservers.delete(observer); load(); }
        }, { root: editor, rootMargin: "300px" });
        projectObservers.add(observer);
        observer.observe(section);
      } else load();
    }
  } catch {
    if (editing === id && !editor.hidden) editorHint.textContent = "Proyek lain belum dapat ditampilkan.";
  }
}

/** #/project/<id> opens the editor; anything else closes it. */
function route() {
  const m = location.hash.match(/^#\/project\/([\w-]+)$/);
  if (m) {
    editor.hidden = false;
    document.body.classList.add("locked");
    editor.scrollTop = 0;
    renderEditor(m[1]);
  } else {
    editing = "";
    projectObservers.forEach((observer) => observer.disconnect());
    projectObservers.clear();
    rowObservers.forEach((observer) => observer.disconnect());
    rowObservers.clear();
    editor.hidden = true;
    editorClips.querySelectorAll("video").forEach((v) => v.pause());
    otherProjects.querySelectorAll("video").forEach((v) => v.pause());
    if (modal.hidden) document.body.classList.remove("locked");
  }
}

window.addEventListener("hashchange", route);
route();
