import { Marquee } from "./marquee";

const backdrop = document.querySelector<HTMLCanvasElement>(".backdrop");
if (backdrop) new Marquee(backdrop, { cyanHero: true });

// Keep the same theme preference as the main app.
const themeButtons = document.querySelectorAll<HTMLButtonElement>(".theme-switch button");
function applyTheme(theme: "dark" | "light") {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  themeButtons.forEach((button) => button.setAttribute("aria-checked", String(button.dataset.choice === theme)));
  try { localStorage.setItem("clipy-theme", theme); } catch { /* Theme lasts for this visit. */ }
  window.dispatchEvent(new Event("themechange"));
}
themeButtons.forEach((button) => button.addEventListener("click", () => applyTheme(button.dataset.choice === "light" ? "light" : "dark")));
applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");

// Match the main app's letter-by-letter block buttons.
document.querySelectorAll<HTMLElement>("[data-block-label]").forEach((button) => {
  const label = button.dataset.blockLabel ?? "";
  button.replaceChildren(...[...label].map((character) => {
    const cell = document.createElement("span");
    cell.textContent = character;
    if (character === " ") cell.className = "gap";
    cell.setAttribute("aria-hidden", "true");
    return cell;
  }));
});
