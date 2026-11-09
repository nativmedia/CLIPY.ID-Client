import { el, type Project } from "./editor";
import "./film-concat.css";

type SequenceStatus = {
  status: "idle" | "running" | "done" | "error";
  progress: number;
  message: string;
};

/** Controls for joining the finished Filming clips without changing the originals. */
export function filmConcatControls(project: Project): HTMLElement | null {
  if (project.settings.mode !== "filming") return null;

  const expected = project.settings.film?.episodes ?? project.settings.clip.count;
  const numbered = [...project.clips].sort((a, b) => a.n - b.n);
  const ready = project.status === "done" && expected > 0 && numbered.length === expected &&
    numbered.every((clip, index) => clip.n === index + 1 && clip.status === "done" && !!clip.video);
  const endpoint = `/api/projects/${encodeURIComponent(project.id)}/film-sequence`;
  const button = el("button", { type: "button", className: "text-button", textContent: "Gabungkan clip", disabled: true });
  const download = el("a", { className: "text-button", href: `${endpoint}/download`, download: "", textContent: "Download hasil gabungan", hidden: true });
  const progress = el("progress", { max: 1, value: 0, hidden: true });
  progress.setAttribute("aria-label", "Progres penggabungan clip");
  const note = el("p", { className: "film-concat-note", textContent: ready ? "Memeriksa status penggabungan…" : "Selesaikan atau ulangi clip yang belum selesai atau gagal sebelum menggabungkan." });
  note.setAttribute("role", "status");
  note.setAttribute("aria-live", "polite");
  const wrap = el("div", { className: "film-concat" }, button, download, progress, note);

  function showStatus(data: SequenceStatus) {
    const running = data.status === "running";
    button.disabled = !ready || running;
    button.textContent = running ? "Menggabungkan…" : "Gabungkan clip";
    download.hidden = !ready || data.status !== "done";
    progress.hidden = !running;
    if (running) progress.value = Math.max(0, Math.min(1, Number.isFinite(data.progress) ? data.progress : 0));
    if (!ready) {
      note.textContent = "Selesaikan atau ulangi clip yang belum selesai atau gagal sebelum menggabungkan.";
    } else if (running) {
      note.textContent = `${data.message || "Menggabungkan clip"} · ${Math.round(progress.value * 100)}%`;
    } else if (data.status === "done") {
      note.textContent = data.message || "Hasil gabungan siap diunduh. Clip satuan tetap tersedia.";
    } else if (data.status === "error") {
      note.textContent = data.message || "Penggabungan gagal. Periksa format clip lalu coba lagi.";
    } else {
      note.textContent = data.message || "Gabungkan seluruh clip sesuai urutan tanpa thumbnail.";
    }
    note.classList.toggle("error", data.status === "error");
  }

  async function updateStatus() {
    if (!wrap.isConnected) return;
    try {
      const response = await fetch(endpoint, { cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const data = await response.json() as SequenceStatus;
      if (!wrap.isConnected) return;
      showStatus(data);
      if (data.status === "running") window.setTimeout(updateStatus, 1000);
    } catch {
      if (!wrap.isConnected) return;
      button.disabled = true;
      note.textContent = "Status penggabungan tidak dapat diperiksa. Menghubungkan kembali…";
      window.setTimeout(updateStatus, 5000);
    }
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    note.textContent = "Menyiapkan penggabungan…";
    try {
      const response = await fetch(endpoint, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : `status ${response.status}`);
      }
      const data = await response.json() as SequenceStatus;
      if (!wrap.isConnected) return;
      showStatus(data);
      if (data.status === "running") window.setTimeout(updateStatus, 1000);
    } catch (err) {
      if (!wrap.isConnected) return;
      button.disabled = !ready;
      note.textContent = `Penggabungan gagal: ${(err as Error).message}`;
      note.classList.add("error");
    }
  });

  // The card is attached after it has been built; stop polling if it is replaced.
  window.setTimeout(updateStatus, 0);
  return wrap;
}
