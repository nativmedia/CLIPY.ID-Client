import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import { pipeline } from "node:stream/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const server = fileURLToPath(new URL("../server/", import.meta.url));
const venv = join(server, ".venv");
const windows = process.platform === "win32";
const python = join(venv, windows ? "Scripts/python.exe" : "bin/python");
const pythonCheck = ["-c", "import sys; sys.exit(sys.version_info[:2] != (3, 12))"];
const file = async (path) => {
  try {
    return (await stat(path)).isFile();
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return false;
  }
};

const models = [
  [
    "face_landmarker.task",
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
  ],
  [
    "face_detection_yunet_2023mar.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
  ],
];

function probe(command, args) {
  return spawnSync(command, args, { encoding: "utf8", timeout: 15000, windowsHide: true });
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit", cwd: server, windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(command + " gagal. Periksa pesan di atas.");
}

async function installModel(name, url, sha256) {
  const directory = join(server, "models");
  const destination = join(directory, name);
  if (await file(destination)) {
    console.log("  Sudah tersedia · " + name);
    return;
  }

  await mkdir(directory, { recursive: true });
  const temporary = join(directory, ".download-" + randomUUID());
  try {
    console.log("Mengunduh " + name + "...");
    const response = await fetch(url, { signal: AbortSignal.timeout(120000) });
    if (!response.ok || !response.body) throw new Error("Unduhan gagal: HTTP " + response.status);
    const total = Number(response.headers.get("content-length"));
    const knownSize = total > 0 && !response.headers.get("content-encoding");
    const hash = createHash("sha256");
    let received = 0;
    let lastUpdate = 0;
    try {
      await pipeline(response.body, async function* (chunks) {
        for await (const chunk of chunks) {
          hash.update(chunk);
          received += chunk.length;
          if (process.stdout.isTTY && Date.now() - lastUpdate >= 100) {
            const percent = Math.min(99, Math.floor(received / total * 100));
            const filled = Math.floor(percent / 5);
            const progress = knownSize
              ? "[" + "█".repeat(filled) + "░".repeat(20 - filled) + "] " + percent + "%"
              : (received / 1048576).toFixed(2) + " MiB";
            process.stdout.write("\r  " + progress);
            lastUpdate = Date.now();
          }
          yield chunk;
        }
      }, createWriteStream(temporary, { flags: "wx" }));
    } finally {
      if (lastUpdate) process.stdout.write("\n");
    }
    if (hash.digest("hex") !== sha256) throw new Error("Checksum model tidak cocok: " + name);
    await rename(temporary, destination);
    console.log("  [████████████████████] 100% · " + (received / 1048576).toFixed(2) + " MiB · Terverifikasi");
  } finally {
    await rm(temporary, { force: true });
  }
}

try {
  console.log("\nCLIPY.ID · Penyiapan desktop\n");
  console.log("[1/3] Memeriksa FFmpeg dan Python");
  const filters = probe("ffmpeg", ["-hide_banner", "-filters"]);
  const encoders = probe("ffmpeg", ["-hide_banner", "-encoders"]);
  if (!/\bass\s+V->V\b/.test(filters.stdout ?? "") ||
      !/\blibx264\b/.test(encoders.stdout ?? "") ||
      probe("ffprobe", ["-version"]).status !== 0) {
    throw new Error("Siapkan FFmpeg dengan libass, libx264, dan ffprobe sesuai README.");
  }

  if (await file(python)) {
    if (probe(python, pythonCheck).status !== 0) {
      throw new Error("Lingkungan server/.venv harus memakai Python 3.12. Pindahkan .venv lama sebelum menjalankan setup lagi.");
    }
  } else {
    const candidates = windows ? [["py", "-3.12"], ["python"]] : [["python3.12"], ["python3"], ["python"]];
    const interpreter = candidates.find(([command, ...args]) => probe(command, [...args, ...pythonCheck]).status === 0);
    if (!interpreter) throw new Error("Python 3.12 belum ditemukan. Pasang Python 3.12 dan buka terminal baru.");
    const [command, ...args] = interpreter;
    run(command, [...args, "-m", "venv", venv]);
  }

  console.log("\n[2/3] Memasang dependensi Python");
  run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "-r", "requirements.txt"]);
  console.log("\n[3/3] Menyiapkan model crop");
  for (const model of models) await installModel(...model);
  console.log("Dependensi CLIPY.ID siap.");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
