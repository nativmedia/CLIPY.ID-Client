import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";
import { createServer as createSocket } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { createServer as createVite } from "vite";

const web = fileURLToPath(new URL("../", import.meta.url));
const server = fileURLToPath(new URL("../../server/", import.meta.url));
const windows = process.platform === "win32";
const python = join(server, ".venv", windows ? "Scripts/python.exe" : "bin/python");
const require = createRequire(import.meta.url);
const tauriCli = require.resolve("@tauri-apps/cli/tauri.js");
const apiUrl = "http://127.0.0.1:4758";

let api, frontend, desktop, signal;
const abort = new AbortController();
const onInterrupt = () => stop("SIGINT");
const onTerminate = () => stop("SIGTERM");
process.once("SIGINT", onInterrupt);
process.once("SIGTERM", onTerminate);

function stop(received) {
  signal = received;
  abort.abort();
}

function start(command, args, cwd, stdin = "inherit") {
  const child = spawn(command, args, { cwd, stdio: [stdin, "inherit", "inherit"], windowsHide: true });
  const exited = once(child, "exit").then(([code, exitSignal]) => ({ code, exitSignal }));
  // The child may stop while the launcher is waiting for the other service.
  void exited.catch(() => {});
  return { child, exited };
}

function killTree(service) {
  if (!service?.child.pid || service.child.exitCode !== null || service.child.signalCode !== null) return;
  if (windows) {
    spawnSync("taskkill.exe", ["/PID", String(service.child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
  } else {
    service.child.kill("SIGTERM");
  }
}

async function assertApiPortFree() {
  const socket = createSocket();
  try {
    socket.listen(4758, "127.0.0.1");
    await once(socket, "listening");
  } catch (error) {
    if (error.code !== "EADDRINUSE") throw error;
    throw new Error("Port 4758 sedang digunakan. Tutup sesi desktop CLIPY.ID yang lama.");
  } finally {
    socket.close();
  }
}

async function waitForApi() {
  for (let attempt = 0; attempt < 180; attempt++) {
    abort.signal.throwIfAborted();
    try {
      const response = await fetch(apiUrl + "/openapi.json", { signal: AbortSignal.timeout(1000) });
      if (response.ok && (await response.json()).info?.title === "CLIPY.ID") return;
    } catch {
      // Uvicorn is still starting.
    }
    await delay(500, undefined, { signal: abort.signal });
  }
  throw new Error("Server CLIPY.ID belum siap. Periksa pesan di terminal.");
}

try {
  if (!existsSync(python)) throw new Error("Jalankan npm run setup sebelum membuka CLIPY.ID.");
  if (spawnSync("cargo", ["--version"], { stdio: "ignore", windowsHide: true }).status !== 0) {
    throw new Error("Cargo belum ditemukan. Pasang Rust sesuai README.");
  }
  await assertApiPortFree();
  frontend = await createVite({
    root: web,
    server: { port: 4757, strictPort: true, proxy: { "/api": apiUrl } },
  });
  await frontend.listen();
  api = start(python, ["desktop_dev.py"], server, "pipe");
  api.child.stdin.on("error", () => {});
  const apiStopped = api.exited.then(({ code }) => {
    throw new Error("Server CLIPY.ID berhenti (kode " + code + "). Periksa pesan di terminal.");
  });
  await Promise.race([waitForApi(), apiStopped]);
  abort.signal.throwIfAborted();

  desktop = start(process.execPath, [tauriCli, "dev", "--no-watch"], web);
  const result = await Promise.race([
    desktop.exited,
    apiStopped,
    once(abort.signal, "abort"),
  ]);
  if (!signal && (result.code !== 0 || result.exitSignal)) {
    throw new Error("Jendela desktop CLIPY.ID berhenti. Periksa pesan di terminal.");
  }
} catch (error) {
  if (!signal) {
    console.error(error.message);
    process.exitCode = 1;
  }
} finally {
  abort.abort();
  if (signal && desktop) {
    // Let Tauri process the terminal interrupt before stopping its remaining children.
    await Promise.race([desktop.exited.catch(() => {}), delay(1500, undefined, { ref: false })]);
  }
  killTree(desktop);
  api?.child.stdin.end();
  if (api) {
    await Promise.race([api.exited.catch(() => {}), delay(12000, undefined, { ref: false })]);
    killTree(api);
  }
  await frontend?.close();
  process.removeListener("SIGINT", onInterrupt);
  process.removeListener("SIGTERM", onTerminate);
}
