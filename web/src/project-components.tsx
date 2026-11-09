/** React islands for project results. The existing form and editor remain in place
 * while their components can be migrated independently in future changes. */
import { useState, type MouseEvent } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { clipLayout, type Clip, type Project } from "./editor";

type LabelGroup = "fonts" | "animations" | "cases" | "title_styles" | "voices" | "brand_styles";
type SettingsProps = {
  project: Project;
  label: (group: LabelGroup, value: string | null | undefined) => string;
  colors: Record<string, string>;
  positions: Record<string, string>;
};

/** Shared project setting summary, independent of the result list implementation. */
export function ProjectSettings({ project: p, label, colors, positions }: SettingsProps) {
  const s = p.settings;
  const transcriptNames: Record<string, string> = {
    youtube_manual: "YouTube transkrip", youtube_auto: "YouTube transkrip",
    whisper: "Lokal transkrip",
  };
  const b = s.brand;
  const brand = b && (p.has_logo || b.text)
    ? [p.has_logo ? "logo" : "", b.text ? `"${b.text}"${b.style ? ` ${label("brand_styles", b.style)}` : ""}` : ""].filter(Boolean).join(" + ")
    : "";
  return <>
    <dt>clip</dt><dd>{`${clipLayout(s)} · ${s.clip.count} clip${s.clip.count > 1 ? "s" : ""} · ≤${s.clip.max_duration}s`}</dd>
    <dt>caption</dt><dd>{`${label("fonts", s.caption.font)} · ${label("animations", s.caption.animation)} · ${s.caption.max_lines ?? 2} baris · `}<span className="dot" style={{ background: colors[s.caption.color] ?? s.caption.color }} />{label("cases", s.caption.case)}</dd>
    <dt>thumbnail</dt><dd>{`${s.thumb.source} · ${label("title_styles", s.thumb.title_style)}${s.mode === "filming" ? "" : ` · ${s.thumb.voice ? label("voices", s.thumb.voice).split(" · ")[0] : "no dub"}`}`}</dd>
    {p.transcript_origin && transcriptNames[p.transcript_origin] && <><dt>transkrip</dt><dd>{transcriptNames[p.transcript_origin]}</dd></>}
    {p.transcription_backend && <><dt>perangkat</dt><dd>{p.transcription_backend}</dd></>}
    {p.transcription_note && <><dt>info perangkat</dt><dd>{p.transcription_note}</dd></>}
    {brand && b && <><dt>brand</dt><dd>{`${brand} · ${b.opacity ?? 100}% · ${positions[b.position] ?? b.position}`}</dd></>}
  </>;
}

type ResultsProps = {
  project: Project;
  onPreview: (project: Project, clip: Clip) => void;
  onRetry: (projectId: string, number: number) => Promise<unknown>;
  onRefresh: () => void;
};

function ClipResult({ project, clip, onPreview, onRetry, onRefresh }: ResultsProps & { clip: Clip }) {
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  const running = !["done", "error", "cancelled"].includes(project.status);
  const errorText = clip.error?.includes("name 'film' is not defined") ? "Kesalahan pembacaan rasio video" : clip.error;
  const onEnter = (event: MouseEvent<HTMLElement>) => {
    const name = event.currentTarget;
    const original = name.querySelector<HTMLElement>(".title-original");
    const box = name.querySelector<HTMLElement>(".clip-box");
    if (!original || !box || original.getBoundingClientRect().width - box.clientWidth <= 2) return;
    const distance = original.getBoundingClientRect().width + 32;
    name.style.setProperty("--read-shift", `${-distance}px`);
    name.style.setProperty("--read-time", `${Math.max(8, distance / 22)}s`);
    name.classList.add("reading");
  };
  const text = <span className="clip-box"><span className="scroll"><span className="title-original">{clip.title}</span><span className="title-copy" aria-hidden="true">{clip.title}</span></span></span>;
  const titleProps = { onMouseEnter: onEnter, onMouseLeave: (event: MouseEvent<HTMLElement>) => event.currentTarget.classList.remove("reading") };
  const retry = async () => {
    setRetrying(true);
    try { await onRetry(project.id, clip.n); onRefresh(); }
    catch (error) { setRetryError(error instanceof Error ? error.message : String(error)); setRetrying(false); }
  };
  return <li className={clip.status === "error" ? "has-error" : ""}>
    <span className="n">{String(clip.n).padStart(2, "0")}</span>
    {clip.video ? <button type="button" className="t link-title" title={`preview · ${clip.title}`} onClick={() => onPreview(project, clip)} {...titleProps}>{text}</button> : <span className="t" title={clip.title} {...titleProps}>{text}</span>}
    {clip.status === "error" && running ? <span className="state">tunggu selesai</span>
      : clip.status === "error" && project.status !== "cancelled" ? <button type="button" className="text-button warn" title={clip.error ?? ""} disabled={retrying} onClick={retry}>{retrying ? "retrying…" : "retry"}</button>
      : clip.video ? <a className="text-button" href={clip.video} download="">download</a>
      : <span className="state">{project.status === "cancelled" ? "dihentikan" : clip.status}</span>}
    {clip.status === "error" && <span className="clip-error" role="status">{retryError || errorText || "Klip gagal diproses"}</span>}
  </li>;
}

/** Shared result list for completed and in-progress projects. */
export function ClipResults(props: ResultsProps) {
  return <>{props.project.clips.map((clip) => <ClipResult key={clip.n} {...props} clip={clip} />)}</>;
}

// Islands mount into existing elements, preserving CSS selectors and semantics.
// Keep ownership so the polling renderer can unmount when a card is removed.
const mounted = new WeakMap<Element, Root>();
function mount(container: HTMLElement, component: React.ReactNode) {
  container.dataset.reactIsland = "";
  const root = createRoot(container);
  mounted.set(container, root);
  flushSync(() => root.render(component));
}

export function disposeProjectIslands(card: Element) {
  card.querySelectorAll("[data-react-island]").forEach((container) => {
    mounted.get(container)?.unmount();
    mounted.delete(container);
  });
}

export function mountProjectSettings(props: SettingsProps): HTMLDListElement {
  const container = document.createElement("dl");
  mount(container, <ProjectSettings {...props} />);
  return container;
}

export function mountClipResults(props: ResultsProps): HTMLOListElement {
  const container = document.createElement("ol");
  container.className = "clips";
  mount(container, <ClipResults {...props} />);
  return container;
}
