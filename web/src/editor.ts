/**
 * Result views: the clip preview pop-up and the project editor page, where each finished
 * clip sits next to ready-made titles and descriptions per platform (editable, saved back).
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
    film?: { episodes: number; max_duration: number; style: string; voice: string; story_mode?: "serial" | "standalone"; story_summary?: string; source_ratio?: string; background?: string; additional_prompt?: string } | null;
    mood?: string;
    clip: { ratio: string; fit: string; count: number; max_duration: number; dubbing_speed?: number };
    caption: { font: string; animation: string; color: string; case: string; position?: number | null; display?: string | null; text_style?: string; max_lines?: number };
    thumb: { source: string; title_style: string; text?: string; numbered?: boolean; sequence?: string | null; position?: string | null; voice: string | null };
    brand?: { position: string; text?: string; style?: string; opacity?: number };
  };
  has_logo?: boolean;
  clips: Clip[];
};

type Platforms = Record<string, { fields: Record<string, number> }>;
const platformLabels = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", facebook: "Facebook", x: "X" };
type Platform = keyof typeof platformLabels;
type Posts = Record<string, Record<string, Record<string, string>>>;

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

/** Browser download starts after the server has combined the completed clips. */
export function sequenceLink(p: Project): HTMLAnchorElement | null {
  if (!["done", "error", "cancelled"].includes(p.status) || !p.clips.some((c) => c.status === "done" && c.video)) return null;
  return el("a", { className: "text-button sequence-download", href: `/api/projects/${encodeURIComponent(p.id)}/clip-sequence`, download: "", textContent: "download clip sequence", title: "Gabungkan semua klip yang selesai tanpa intro thumbnail; pembuatan dapat memakan waktu" });
}

let platforms: Platforms | null = null;
async function getPlatforms(): Promise<Platforms> {
  if (!platforms) platforms = (await (await fetch("/api/options", { cache: "no-store" })).json()).platforms as Platforms;
  return platforms;
}

// ---------- preview pop-up ----------

const modal = $<HTMLDivElement>("#preview-modal");
const modalVideo = $<HTMLVideoElement>("#modal-video");
const modalTitle = $<HTMLParagraphElement>("#modal-title");
const modalDownload = $<HTMLAnchorElement>("#modal-download");
const modalEdit = $<HTMLButtonElement>("#modal-edit");
let modalProject = "";

export function openPreview(project: Project, clip: Clip) {
  if (!clip.video) return;
  modalProject = project.id;
  modalVideo.src = clip.video;
  modalVideo.poster = clip.thumb ?? "";
  modalVideo.style.aspectRatio = project.settings.clip.ratio.replace(":", " / ");
  modalTitle.textContent = clip.title;
  modalDownload.href = clip.video;
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
const editorHint = $<HTMLParagraphElement>("#editor-hint");
let editing = "";
const pendingSaves = new Map<string, { timer: number; posts: Posts }>();

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

function queueSave(n: string, platform: string, field: string, text: string, status: HTMLElement) {
  const projectId = editing;
  const save = pendingSaves.get(projectId) ?? { timer: 0, posts: {} };
  ((save.posts[n] ??= {})[platform] ??= {})[field] = text;
  pendingSaves.set(projectId, save);
  status.textContent = "saving…";
  clearTimeout(save.timer);
  save.timer = window.setTimeout(async () => {
    pendingSaves.delete(projectId);
    try {
      const res = await fetch(`/api/projects/${projectId}/posts`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ posts: save.posts }),
      });
      if (!res.ok) throw new Error(String(res.status));
      status.textContent = "saved";
    } catch {
      status.textContent = "couldn't save";
    }
  }, 700);
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

/** Platform tabs plus their fields for one clip; `posts` may still be loading. */
function postsPanel(n: string, spec: Platforms, posts: Posts | null): HTMLElement {
  const box = el("div", { className: "posts" });
  const tabs = el("div", { className: "tabs" });
  tabs.setAttribute("role", "tablist");
  const body = el("div", { className: "post-fields" });
  const status = el("span", { className: "save-status" });

  if (!posts) {
    box.append(el("p", { className: "hint", textContent: "writing descriptions…" }));
    return box;
  }
  const show = (platform: Platform) => {
    tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.platform === platform)));
    const limit = spec[platform].fields.description;
    const values = ((posts[n] ??= {})[platform] ??= {});
    const area = el("textarea", {
      value: values.description ?? "", maxLength: limit, spellcheck: true, rows: platform === "x" ? 3 : 5,
    });
    area.setAttribute("aria-label", `Deskripsi ${platformLabels[platform]}`);
    const count = el("span", { className: "count", textContent: `${area.value.length}/${limit}` });
    const copyButton = el("button", { type: "button", className: "text-button", textContent: "copy" });
    copyButton.addEventListener("click", () => copy(area.value, copyButton));
    area.addEventListener("input", () => {
      count.textContent = `${area.value.length}/${limit}`;
      values.description = area.value;
      queueSave(n, platform, "description", area.value, status);
    });
    body.replaceChildren(el("div", { className: "post-field" },
      el("div", { className: "field-head" }, el("span", { textContent: "description" }), count, copyButton),
      area,
    ));
  };

  for (const platform of Object.keys(platformLabels) as Platform[]) {
    const b = el("button", { type: "button", textContent: platformLabels[platform] });
    b.dataset.platform = platform;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => show(platform));
    tabs.append(b);
  }
  box.append(tabs, body, status);
  show("youtube");
  return box;
}

function clipCard(p: Project, c: Clip, spec: Platforms, posts: Posts | null): HTMLLIElement {
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
  if (c.video) actions.append(el("a", { className: "text-button", href: c.video, download: "", textContent: "download mp4" }));
  if (c.thumb) actions.append(el("a", { className: "text-button", href: c.thumb, download: "", textContent: "download thumbnail" }));
  if (c.srt) actions.append(el("a", { className: "text-button", href: c.srt, download: "", textContent: "srt" }));
  if (c.status === "error") {
    const retry = el("button", { type: "button", className: "text-button warn", textContent: "retry" });
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      retry.textContent = "retrying…";
      try {
        await retryClip(p.id, c.n);
        followUntilDone(p.id);
      } catch (err) {
        retry.textContent = `retry failed: ${(err as Error).message}`;
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
      c.video ? postsPanel(String(c.n), spec, posts) : null,
    ),
  );
}

let follow = 0;
/** After a retry, re-render the editor until the clip settles. */
function followUntilDone(id: string) {
  clearTimeout(follow);
  follow = window.setTimeout(async () => {
    if (editing !== id) return;
    const p: Project = await (await fetch(`/api/projects/${id}`)).json();
    if (p.status === "done" || p.status === "error") renderEditor(id);
    else followUntilDone(id);
  }, 2000);
}

async function renderEditor(id: string) {
  editing = id;
  editorHint.textContent = "";
  editorHint.classList.remove("error");
  const [res, spec] = await Promise.all([fetch(`/api/projects/${id}`), getPlatforms()]);
  if (!res.ok) {
    editorClips.replaceChildren();
    $<HTMLElement>("#editor-story").replaceChildren();
    editorHint.textContent = "project not found";
    editorHint.classList.add("error");
    return;
  }
  const p: Project = await res.json();
  $<HTMLImageElement>("#editor-thumb").src = p.thumbnail;
  $<HTMLElement>("#editor-title").textContent = p.title || p.url;
  const s = p.settings;
  $<HTMLElement>("#editor-meta").replaceChildren(
    `${s.clip.ratio} ${s.clip.fit === "crop" ? "crop" : "no crop"} · ${p.clips.filter((c) => c.status === "done").length}/${p.clips.length} clips · `,
    p.url ? el("a", { href: p.url, target: "_blank", rel: "noopener", textContent: "source video" }) : "uploaded video",
  );
  $<HTMLElement>("#editor-sequence").replaceChildren(...[sequenceLink(p)].filter((node): node is HTMLAnchorElement => node !== null));
  const story = s.mode === "filming" ? s.film?.story_summary?.trim() : "";
  $<HTMLElement>("#editor-story").replaceChildren(...(story ? [el("details", { className: "editor-story" },
    el("summary", { textContent: "Lihat rangkuman cerita utuh" }),
    el("p", { textContent: story }),
  )] : []));

  const draw = (posts: Posts | null) => editorClips.replaceChildren(...p.clips.map((c) => clipCard(p, c, spec, posts)));
  draw(null);
  if (!p.clips.some((c) => c.video)) return;
  try {
    const posts = await fetch(`/api/projects/${id}/posts`);
    const data = await posts.json();
    if (editing !== id) return;
    if (!posts.ok) throw new Error(data.detail ?? `status ${posts.status}`);
    draw(data);
  } catch (err) {
    editorHint.textContent = `titles & descriptions: ${(err as Error).message}`;
    editorHint.classList.add("error");
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
    editor.hidden = true;
    editorClips.querySelectorAll("video").forEach((v) => v.pause());
    if (modal.hidden) document.body.classList.remove("locked");
  }
}

window.addEventListener("hashchange", route);
route();
