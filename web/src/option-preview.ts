import { el } from "./editor";

/** The native select still owns the value and validation used by the form. */
export function previewSelect(select: HTMLSelectElement, group: string) {
  const field = select.parentElement!;
  const label = field.querySelector("span")!;
  select.hidden = true;
  const trigger = el("button", { className: "option-trigger", type: "button" });
  const list = el("div", { className: "option-list", id: `${select.id}-options`, hidden: true });
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", label.textContent ?? "");
  trigger.setAttribute("aria-controls", list.id);
  trigger.setAttribute("aria-haspopup", "listbox");
  field.classList.add("preview-pick");
  if (group === "font") field.classList.add("font-pick");
  if (group === "font-weight") field.classList.add("font-weight-pick");
  select.after(trigger, list);

  const sync = () => {
    trigger.textContent = select.selectedOptions[0]?.text ?? "choose";
    if (group === "font") trigger.dataset.font = select.value;
    trigger.disabled = select.disabled;
    trigger.title = select.title;
    field.classList.toggle("is-disabled", select.disabled);
    list.setAttribute("aria-label", label.textContent ?? "");
    trigger.setAttribute("aria-label", `${label.textContent}: ${trigger.textContent}`);
    trigger.setAttribute("aria-disabled", String(select.disabled));
    trigger.setAttribute("aria-expanded", String(!list.hidden));
    trigger.setAttribute("aria-invalid", select.getAttribute("aria-invalid") ?? "false");
  };
  const close = (focus = false) => {
    list.hidden = true;
    sync();
    if (focus) trigger.focus({ preventScroll: true });
  };
  const show = () => {
    if (select.disabled) return;
    if (!list.hidden) return close(true);
    const options = Array.from(select.options).filter((option) => !option.disabled && !option.hidden);
    if (!options.length) return;
    const buttons = options.map((option) => {
      const styledName = !["font", "font-weight"].includes(group) && option.value !== "none" && !option.value.startsWith("word-");
      const button = el("button", { type: "button", className: "option-preview" },
        styledName ? null : el("span", { textContent: option.text }),
        styledName ? el("img", { src: `/option-previews/${group}-${encodeURIComponent(option.value)}.webp`, alt: "", width: 160, height: 44 }) : null,
      );
      if (styledName) button.setAttribute("aria-label", option.text);
      if (group === "font") button.dataset.font = option.value;
      if (group === "font-weight") button.dataset.fontWeight = option.value;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(option.selected));
      button.tabIndex = -1;
      button.addEventListener("click", () => {
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        close(true);
      });
      return button;
    });
    list.replaceChildren(...buttons);
    list.style.transform = "";
    list.hidden = false;
    sync();
    const bounds = list.getBoundingClientRect();
    const horizontal = Math.max(8 - bounds.left, Math.min(0, window.innerWidth - bounds.right - 8));
    const shift = Math.max(8 - bounds.top, Math.min(0, window.innerHeight - bounds.bottom - 8));
    const above = trigger.getBoundingClientRect().top - bounds.bottom;
    const vertical = shift < 0 && bounds.top + above >= 8 ? above : shift;
    list.style.transform = `translate(${horizontal}px, ${vertical}px)`;
    (buttons.find((button) => button.getAttribute("aria-selected") === "true") ?? buttons[0]).focus({ preventScroll: true });
  };
  trigger.addEventListener("click", show);
  trigger.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    if (list.hidden) show();
  });
  list.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    const buttons = Array.from(list.querySelectorAll("button"));
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: buttons.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    buttons[Math.max(0, Math.min(buttons.length - 1, next))]?.focus();
  });
  field.addEventListener("focusout", (event) => {
    if (!field.contains(event.relatedTarget as Node | null)) close();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!list.hidden && !field.contains(event.target as Node)) close();
  });
  sync();
  return sync;
}

