/* Fixed glyph anchors and a seekable timeline, shared by preview and rendering. */
const config = JSON.parse(document.getElementById("overlay-config").textContent);
const timeline = gsap.timeline({paused: true});
timeline.to({}, {duration: config.duration});

for (const cue of config.cues || []) {
  const group = document.getElementById(cue.id);
  timeline.set(group, {visibility: "visible"}, cue.start);
  timeline.set(group, {visibility: "hidden"}, cue.end);
  for (const word of cue.words) {
    const glyph = document.querySelector("#" + word.id + " .glyph");
    const accent = document.querySelector("#" + word.id + " .accent");
    const length = Math.max(0.001, word.end - word.start);
    const paint = {color: config.color};
    switch (config.animation) {
      case "box":
        timeline.set(accent, {opacity: 1}, word.start);
        timeline.set(glyph, {color: "#000000", webkitTextStrokeWidth: "0px"}, word.start);
        timeline.set(accent, {opacity: 0}, word.end);
        break;
      case "underline":
        timeline.fromTo(accent, {scaleX: 0, opacity: 1}, {scaleX: 1, duration: Math.min(.12, length), immediateRender: false}, word.start);
        timeline.set(accent, {opacity: 0}, word.end);
        timeline.set(glyph, paint, word.start);
        break;
      case "glow":
        timeline.set(accent, {opacity: 1}, word.start);
        timeline.set(accent, {opacity: 0}, word.end);
        timeline.set(glyph, paint, word.start);
        break;
      case "pop":
        timeline.fromTo(glyph, {scale: .90, color: config.color},
          {scale: 1, duration: Math.min(.14, length), ease: "back.out(1.4)", immediateRender: false}, word.start);
        break;
      case "karaoke":
        timeline.fromTo(accent, {clipPath: "inset(0 100% 0 0)", opacity: 1},
          {clipPath: "inset(0 0% 0 0)", duration: length, ease: "none", immediateRender: false}, word.start);
        timeline.set(accent, {opacity: 0}, word.end);
        break;
      case "reveal":
        timeline.fromTo(glyph, {opacity: 0}, {opacity: 1, color: config.color,
          duration: Math.min(.09, length), immediateRender: false}, word.start);
        break;
      case "fade":
        timeline.fromTo(glyph, {opacity: 0}, {opacity: 1, color: config.color,
          duration: Math.min(.14, cue.end - cue.start), immediateRender: false}, cue.start);
        const fadeOut = Math.min(.08, (cue.end - cue.start) / 2);
        timeline.to(glyph, {opacity: 0, duration: fadeOut}, cue.end - fadeOut);
        break;
      default:
        timeline.set(glyph, paint, word.start);
    }
    if (!["fade", "reveal", "karaoke"].includes(config.animation)) {
      timeline.set(glyph, {color: "#FFFFFF", webkitTextStrokeWidth: word.outline + "px"}, word.end);
    }
  }
}
window.__timelines = window.__timelines || {};
window.__timelines.main = timeline;
timeline.seek(0);
