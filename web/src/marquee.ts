/**
 * Full-screen ASCII backdrop: a dark cell grid with faint drifting character dust,
 * flocks of tiny birds (schooling like the ascii-sharks fish), and "CLIPY.ID" spelled
 * out in glyphs that keep re-rolling, gliding right along the top of the page and left
 * along the bottom, with glitch scans and sparkles passing through.
 */
import { World } from "./sim/world";

const WORD = "CLIPY.ID";
/** Glyph pools by ink density; a letter's brighter rows draw from the denser pools. */
const POOLS = [
  ";:!|/\\~^-+=<>",
  "*?7tfjlrcvxz{}[]()",
  "XZkdbqpS5%2349aeoun",
  "@#&$8BWMQ0NHDRGK%",
];
/** Anything goes: used for the random re-rolls and glitch noise. */
const NOISE = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const EDGE_HEAVY = ":;+=~";
const EDGE_LIGHT = ".',`-_";
const DUST = ".,'`:;+*~^-=/\\|01";
/** Flock birds: wings up, level, down, plus a few broader silhouettes. */
const BIRD_POSES = ["v", "-", "^", "-"];
const BIRD_ALT = ["w", "~", "V", "Y"];
const ACCENTS = ["#e0892c", "#e03a32", "#b44ae0", "#33c8cc", "#3cb54a", "#3150e8", "#d9c14a"];
/** Cells per second each band glides. */
const SPEED = 2.4;
const FONT = '"Geist Mono", ui-monospace, Menlo, monospace';
const STEP = 1 / 60;
const MAX_STEPS = 4;
/** Flock birds stay faint so they never fight the input for attention. */
const BIRD_ALPHAS = [0.16, 0.26, 0.38, 0.5, 0.64];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Backdrop colours per page theme (set as data-theme on <html>). */
const THEMES = {
  dark: {
    bg: "#000",
    grid: "rgba(255,255,255,0.055)",
    dust: "#c8d2e6",
    bird: (a: number) => `rgba(230,238,255,${a})`,
    sparkle: "#ffffff",
    // Glyph ink for a brightness level 0–255 (brighter is lighter); k tints it toward cyan.
    ink: (level: number, k: number) =>
      `rgb(${Math.round(level * (0.92 - k * 0.5))},${Math.min(255, Math.round(level * (0.97 + k * 0.05)))},${Math.min(255, Math.round(level * 1.05 + 4))})`,
  },
  light: {
    // Warm grey paper and graphite ink, matching the page's soft light theme.
    bg: "#e4e2dd",
    grid: "rgba(51,53,60,0.06)",
    dust: "#7d8088",
    bird: (a: number) => `rgba(51,53,60,${(a * 0.85).toFixed(3)})`,
    sparkle: "#33353c",
    // On paper the scale flips: the "brightest" glyphs are the darkest ink, but never black.
    ink: (level: number, k: number) => {
      const v = clamp(215 - level * 0.62, 70, 175);
      return `rgb(${Math.round(v * (0.98 - k * 0.6))},${Math.round(v * (1 + k * 0.12))},${Math.min(255, Math.round(v * 1.04 + k * 40))})`;
    },
  },
};
type Theme = (typeof THEMES)["dark"];
const currentTheme = (): Theme => (document.documentElement.dataset.theme === "light" ? THEMES.light : THEMES.dark);

function hash3(x: number, y: number, z: number) {
  let h = (x * 374761393 + y * 668265263 + z * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

export class Marquee {
  private ctx: CanvasRenderingContext2D;
  private bg = document.createElement("canvas");
  private dpr = 1;
  private width = 0;
  private height = 0;
  private cw = 12;
  private ch = 18;
  private cols = 0;
  private rows = 0;
  /** Per-cell ink coverage (0–1) of one "CLIPY.ID" plus its trailing gap. */
  private mask = new Float32Array(0);
  private maskW = 0;
  private maskH = 0;
  private bandTop = 0;
  private bandBottom = 0;
  private world: World | null = null;
  private birdBuckets: number[][] = BIRD_ALPHAS.map(() => []);
  /** Glyph for each entry of birdBuckets, kept in step with it. */
  private birdGlyphs: string[][] = BIRD_ALPHAS.map(() => []);
  private theme: Theme = currentTheme();
  private birdStyles = BIRD_ALPHAS.map(this.theme.bird);
  private raf = 0;
  private last = 0;
  private acc = 0;
  private start = performance.now();
  private reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  constructor(private canvas: HTMLCanvasElement, private options: { cyanHero?: boolean } = {}) {
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("2D canvas unavailable");
    this.ctx = ctx;
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.reducedMotion.addEventListener("change", () => this.schedule());

    window.addEventListener("pointermove", (e) => {
      if (!this.world) return;
      const p = this.world.pointer;
      p.x = e.clientX;
      p.y = e.clientY;
      p.active = e.pointerType !== "touch";
    }, { passive: true });
    window.addEventListener("pointerdown", (e) => {
      // Clicks on the backdrop scatter the flock; clicks on the form are left alone.
      if (this.world && !this.reducedMotion.matches && !(e.target as Element).closest(".field, .preview")) {
        this.world.splash(e.clientX, e.clientY);
      }
    }, { passive: true });
    document.documentElement.addEventListener("pointerleave", () => {
      if (this.world) this.world.pointer.active = false;
    });
    window.addEventListener("themechange", () => {
      this.theme = currentTheme();
      this.birdStyles = BIRD_ALPHAS.map(this.theme.bird);
      this.paintBackground();
      if (this.reducedMotion.matches) this.draw(0);
    });

    // Canvas text can't trigger a webfont load, so wait for the face before measuring.
    document.fonts.load(`800 64px ${FONT}`).finally(() => this.resize());
  }

  private resize() {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    this.width = rect.width;
    this.height = rect.height;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.cw = clamp(Math.round(this.width / 124), 7, 13);
    this.ch = Math.round(this.cw * 1.45);
    this.cols = Math.ceil(this.width / this.cw);
    this.rows = Math.ceil(this.height / this.ch);
    // Let the page size and snap things to the same cells.
    const root = document.documentElement.style;
    root.setProperty("--cw", `${this.cw}px`);
    root.setProperty("--ch", `${this.ch}px`);
    window.dispatchEvent(new CustomEvent("gridresize", { detail: { cw: this.cw, ch: this.ch } }));
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.bg.width = this.canvas.width;
    this.bg.height = this.canvas.height;

    // Letters take about a sixth of the screen height each, smaller on short screens.
    this.maskH = clamp(Math.round(this.rows * 0.16), 5, 16);
    this.bandTop = 1;
    this.bandBottom = this.rows - 1 - this.maskH;
    // Where the bands end, so the page can keep its content between them.
    root.setProperty("--band-top", `${(this.bandTop + this.maskH) * this.ch}px`);
    root.setProperty("--band-bottom", `${Math.max(0, this.height - this.bandBottom * this.ch)}px`);
    this.buildMask();
    this.paintBackground();

    // A calmer sky than the ocean demo: fewer birds per pixel, no predators.
    if (!this.world) this.world = new World([], clamp(Math.round((this.width * this.height) / 4800), 60, 280));
    this.world.resize(this.width, this.height, this.cw, this.ch);
    this.schedule();
  }

  /** Rasterise the word once at screen size, then average its alpha per grid cell. */
  private buildMask() {
    const { cw, ch, maskH } = this;
    const off = document.createElement("canvas");
    const g = off.getContext("2d", { willReadFrequently: true })!;
    const capHeight = maskH * ch * 0.96;
    g.font = `800 100px ${FONT}`;
    const probe = g.measureText(WORD);
    const size = (100 * capHeight) / (probe.actualBoundingBoxAscent || 72);
    g.font = `800 ${size}px ${FONT}`;
    const m = g.measureText(WORD);
    const gap = cw * 6;
    this.maskW = Math.ceil((m.width + gap) / cw);
    off.width = this.maskW * cw;
    off.height = maskH * ch;
    g.font = `800 ${size}px ${FONT}`;
    g.fillStyle = "#fff";
    g.textBaseline = "alphabetic";
    g.fillText(WORD, gap / 2, maskH * ch - (maskH * ch - m.actualBoundingBoxAscent) / 2);

    const data = g.getImageData(0, 0, off.width, off.height).data;
    this.mask = new Float32Array(this.maskW * maskH);
    for (let r = 0; r < maskH; r++) {
      for (let c = 0; c < this.maskW; c++) {
        let sum = 0;
        let n = 0;
        for (let y = r * ch; y < (r + 1) * ch; y += 2) {
          for (let x = c * cw; x < (c + 1) * cw; x += 2) {
            sum += data[(y * off.width + x) * 4 + 3];
            n++;
          }
        }
        this.mask[r * this.maskW + c] = sum / (n * 255);
      }
    }
  }

  private paintBackground() {
    const g = this.bg.getContext("2d")!;
    const { width: W, height: H, dpr, cw, ch } = this;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = this.theme.bg;
    g.fillRect(0, 0, W * dpr, H * dpr);
    g.strokeStyle = this.theme.grid;
    g.lineWidth = 1;
    g.beginPath();
    for (let c = 0; c <= Math.ceil(W / cw); c++) {
      const x = Math.round(c * cw * dpr) + 0.5;
      g.moveTo(x, 0);
      g.lineTo(x, H * dpr);
    }
    for (let r = 0; r <= Math.ceil(H / ch); r++) {
      const y = Math.round(r * ch * dpr) + 0.5;
      g.moveTo(0, y);
      g.lineTo(W * dpr, y);
    }
    g.stroke();
  }

  private schedule() {
    cancelAnimationFrame(this.raf);
    if (this.reducedMotion.matches) {
      // Let the flocks settle, then show a single frame.
      if (this.world && this.world.time < 4) for (let i = 0; i < 240; i++) this.world.step(STEP);
      this.draw(0);
      return;
    }
    this.last = performance.now();
    this.raf = requestAnimationFrame(this.frame);
  }

  private frame = (now: number) => {
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    this.acc += dt;
    let steps = 0;
    while (this.world && this.acc >= STEP && steps < MAX_STEPS) {
      this.world.step(STEP);
      this.acc -= STEP;
      steps++;
    }
    if (steps === MAX_STEPS) this.acc = 0;
    this.draw((now - this.start) / 1000);
    this.raf = requestAnimationFrame(this.frame);
  };


  private draw(t: number) {
    const { ctx, dpr, ch } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(this.bg, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    const travel = t * SPEED;
    ctx.font = `500 ${Math.round(ch * 0.66)}px ${FONT}`;
    this.drawDust(travel, t);
    ctx.font = `600 ${Math.round(ch * 0.74)}px ${FONT}`;
    this.drawBirds(travel);
    ctx.font = `800 ${Math.round(ch * 0.8)}px ${FONT}`;
    ctx.save();
    ctx.globalAlpha = 0.4;
    this.drawBand(this.bandTop, travel, t, 1);
    this.drawBand(this.bandBottom, -travel, t, 2);
    ctx.restore();
  }

  /** Ink coverage of a band at screen cell (c, row), for a band shifted by `shift` cells. */
  private coverage(row0: number, shift: number, c: number, row: number) {
    const r = row - row0;
    if (r < 0 || r >= this.maskH) return 0;
    const mx = (((c - Math.floor(shift)) % this.maskW) + this.maskW) % this.maskW;
    return this.mask[r * this.maskW + mx];
  }

  private underText(c: number, r: number, travel: number) {
    return this.coverage(this.bandTop, travel, c, r) >= 0.12 || this.coverage(this.bandBottom, -travel, c, r) >= 0.12;
  }

  /**
   * Sparse random characters scattered over the grid, each breathing in and out on its
   * own clock and swapping to a new character now and then; a few carry an accent.
   */
  private drawDust(travel: number, t: number) {
    const { ctx, cw, ch, cols, rows } = this;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const h = hash3(c, r, 99);
        if (h > 0.016 || this.underText(c, r, travel)) continue;
        const k = h / 0.016;
        const pulse = 0.5 + 0.5 * Math.sin(t * (0.4 + k * 1.6) + k * 40);
        const alpha = 0.04 + 0.26 * pulse * pulse * pulse;
        if (alpha < 0.05) continue;
        const swap = Math.floor(t * (0.3 + hash3(c, r, 5) * 0.9) + k * 17);
        const glyph = DUST[Math.floor(hash3(c, r, swap) * DUST.length)];
        ctx.globalAlpha = alpha;
        ctx.fillStyle = k < 0.12 ? ACCENTS[(c + r) % ACCENTS.length] : this.theme.dust;
        ctx.fillText(glyph, c * cw + cw / 2, r * ch + ch / 2 + 1);
      }
    }
    ctx.globalAlpha = 1;
  }

  /**
   * One glyph per bird, snapped to the grid. The wingbeat cycles "v", "-", "^"; a few
   * birds read as broader silhouettes, and startled ones scramble. Nearer, turning or
   * startled birds glint brighter, and all of them duck behind the lettering.
   */
  private drawBirds(travel: number) {
    const { ctx, cw, ch, cols, rows, birdBuckets, birdGlyphs, world } = this;
    if (!world) return;
    for (let b = 0; b < birdBuckets.length; b++) {
      birdBuckets[b].length = 0;
      birdGlyphs[b].length = 0;
    }
    const taken = new Set<number>();
    const fish = world.fish;
    for (let n = 0; n < fish.length; n++) {
      const f = fish[n];
      if (!f.alive) continue;
      const c = Math.floor(f.x / cw);
      const r = Math.floor(f.y / ch);
      if (c < 0 || r < 0 || c >= cols || r >= rows) continue;
      const i = r * cols + c;
      if (taken.has(i) || this.underText(c, r, travel)) continue;
      taken.add(i);
      const glint = clamp(Math.abs(f.turn) / 3.5, 0, 1);
      const v = 0.3 + (0.4 * (f.depth - 0.55)) / 0.45 + 0.3 * Math.max(glint, f.alarm);
      const b = clamp(Math.floor(v * BIRD_ALPHAS.length), 0, BIRD_ALPHAS.length - 1);
      const beat = Math.floor((((f.phase * 0.6) / (Math.PI * 2)) % 1) * BIRD_POSES.length);
      let glyph = BIRD_POSES[beat];
      if (f.alarm > 0.5) glyph = "<>^v*"[Math.floor(hash3(n, Math.floor(f.phase * 2), 3) * 5)];
      else if (hash3(n, 1, 5) < 0.14) glyph = beat % 2 ? "-" : BIRD_ALT[n % BIRD_ALT.length];
      birdBuckets[b].push(i);
      birdGlyphs[b].push(glyph);
    }
    for (let b = 0; b < birdBuckets.length; b++) {
      const bucket = birdBuckets[b];
      if (!bucket.length) continue;
      ctx.fillStyle = this.birdStyles[b];
      const glyphs = birdGlyphs[b];
      for (let k = 0; k < bucket.length; k++) {
        const i = bucket[k];
        ctx.fillText(glyphs[k], (i % cols) * cw + cw / 2, ((i / cols) | 0) * ch + ch / 2 + 1);
      }
    }
  }

  /**
   * One band of lettering, drawn as bare glyphs with no cell fill. `shift` > 0 moves it
   * right; the fractional part slides the glyphs off-grid so the glide stays smooth.
   *
   * Every glyph re-rolls on its own clock from a pool matched to its brightness (with
   * some pure noise mixed in). On top of that: a scramble scan that sweeps along the band,
   * brief row-slice glitches that jolt sideways, sparkles, and a slow colour drift.
   */
  private drawBand(row0: number, shift: number, t: number, seed: number) {
    const { ctx, cw, ch, cols, mask, maskW, maskH } = this;
    const accents = this.options.cyanHero ? ["#33c8cc", "#7de9e8", "#168e93"] : ACCENTS;
    const sparkle = this.options.cyanHero ? (document.documentElement.dataset.theme === "light" ? "#176f72" : "#b6ffff") : this.theme.sparkle;
    const ink = (level: number, k: number) => {
      if (!this.options.cyanHero) return this.theme.ink(level, k);
      if (document.documentElement.dataset.theme === "light") {
        const v = clamp(155 - level * 0.38, 50, 130);
        return `rgb(${Math.round(v * 0.35)},${Math.round(v * 0.95)},${Math.round(v)})`;
      }
      return `rgb(${Math.round(level * 0.2)},${Math.round(level * 0.82)},${Math.round(level * 0.84)})`;
    };
    const whole = Math.floor(shift);
    const frac = shift - whole;

    // Scramble scan: a short stretch of the band that churns and lights up as it passes.
    const scanSpan = cols * 1.8;
    const scan = ((t * 26 + seed * 211) % scanSpan) - cols * 0.4;
    // Glitch: now and then a slice of rows jumps a few cells sideways for a beat.
    const gTick = Math.floor(t * 5);
    const glitching = hash3(gTick, seed, 77) < 0.07;
    const gRow0 = Math.floor(hash3(gTick, seed, 78) * maskH);
    const gRows = 1 + Math.floor(hash3(gTick, seed, 79) * Math.max(1, maskH / 3));
    const gOffset = (hash3(gTick, seed, 80) < 0.5 ? -1 : 1) * (1 + Math.floor(hash3(gTick, seed, 81) * 3));

    for (let r = 0; r < maskH; r++) {
      const y = (row0 + r) * ch;
      // Lit from the top: upper rows of each letter are brighter and denser.
      const light = 0.95 - 0.5 * (r / Math.max(1, maskH - 1));
      const jolt = glitching && r >= gRow0 && r < gRow0 + gRows ? gOffset : 0;
      for (let c = -1; c <= cols; c++) {
        const mx = (((c - whole) % maskW) + maskW) % maskW;
        const cov = mask[r * maskW + mx];
        if (cov < 0.12) continue;
        const x = (c + frac + jolt) * cw;
        // Keyed to the letter, not the screen, so each glyph rides along with the text.
        const key = (hash3(mx, r, seed) * 4294967295) >>> 0;
        const rate = 0.5 + hash3(key, 1, 1) * 5.5;
        const scanned = Math.abs(c - scan) < 3.5;
        const tick = scanned ? Math.floor(t * 30) : Math.floor(t * rate + hash3(key, 2, 2) * 10);
        const pick = hash3(key, tick, 11);
        const roll = hash3(key, Math.floor(t * 2.4 + (key % 997) / 97), 7);

        if (cov < 0.55) {
          ctx.fillStyle = scanned || roll < 0.08 ? accents[(key + tick) % accents.length] : ink(60 + cov * 110, 0);
          const set = cov >= 0.35 ? EDGE_HEAVY : EDGE_LIGHT;
          ctx.fillText(set[Math.floor(pick * set.length)], x + cw / 2, y + ch / 2 + 1);
          continue;
        }

        const shade = clamp(light + (hash3(mx, r, Math.floor(t * 6)) - 0.5) * 0.1, 0, 1);
        let glyph: string;
        if (scanned || pick < 0.22) {
          glyph = NOISE[Math.floor(hash3(key, tick, 12) * NOISE.length)];
        } else {
          const jitter = ((key >>> 4) % 3) - 1;
          const pool = POOLS[clamp(Math.floor(shade * 3.99) + jitter, 0, POOLS.length - 1)];
          glyph = pool[Math.floor(hash3(key, tick, 13) * pool.length)];
        }

        let color: string;
        if (scanned) {
          color = hash3(key, tick, 14) < 0.5 ? sparkle : accents[(key + tick) % accents.length];
        } else if (hash3(key, Math.floor(t * 8), 21) < 0.01) {
          color = sparkle;
        } else if (roll < 0.11) {
          color = accents[(key + Math.floor(t * 0.7)) % accents.length];
        } else {
          // Cool grey that drifts toward cyan in slow waves along the word.
          const level = 90 + shade * 150;
          const k = 0.35 * (0.5 + 0.5 * Math.sin(mx * 0.09 + t * 0.6 + seed * 2));
          color = ink(level, k);
        }
        ctx.fillStyle = color;
        ctx.fillText(glyph, x + cw / 2, y + ch / 2 + 1);
      }
    }
  }
}
