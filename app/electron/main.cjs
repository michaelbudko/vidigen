// Electron main process: owns the window and the Python engine child process.
const { app, BrowserWindow, ipcMain, dialog, shell, protocol, net } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const isWin = process.platform === "win32";
const engineDir = app.isPackaged
  ? path.join(process.resourcesPath, "engine")
  : path.resolve(__dirname, "..", "..", "engine");

let engine = null;
let engineInfo = null; // { url, token }
let engineReady = null;
let win = null;

protocol.registerSchemesAsPrivileged([
  { scheme: "media", privileges: { stream: true, supportFetchAPI: true, bypassCSP: true } },
]);

function findPython() {
  if (process.env.VIDIGEN_PYTHON) return process.env.VIDIGEN_PYTHON;
  const venv = isWin
    ? path.join(engineDir, ".venv", "Scripts", "python.exe")
    : path.join(engineDir, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return isWin ? "python" : "python3";
}

function startEngine() {
  const token = crypto.randomBytes(24).toString("base64url");
  engine = spawn(findPython(), ["-m", "vidigen_engine", "serve"], {
    cwd: engineDir,
    env: { ...process.env, VIDIGEN_TOKEN: token, PYTHONUNBUFFERED: "1" },
    windowsHide: true,
  });
  engineReady = new Promise((resolve, reject) => {
    let buf = "";
    let errTail = "";
    engine.stdout.on("data", (chunk) => {
      buf += chunk.toString();
      const m = buf.match(/VIDIGEN_READY port=(\d+)/);
      if (m && !engineInfo) {
        engineInfo = { url: `http://127.0.0.1:${m[1]}`, token };
        resolve(engineInfo);
      }
    });
    engine.stderr.on("data", (chunk) => {
      errTail = (errTail + chunk.toString()).slice(-4000);
      process.stderr.write(chunk);
    });
    engine.on("error", (e) => reject(new Error(`Could not start the Python engine: ${e.message}`)));
    engine.on("exit", (code) => {
      if (!engineInfo) reject(new Error(`Engine exited (${code}).\n${errTail}`));
      engine = null;
    });
  });
  return engineReady;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: "#0b0b10",
    title: "Vidigen",
    titleBarStyle: isWin ? "hidden" : "default",
    titleBarOverlay: isWin ? { color: "#0b0b10", symbolColor: "#c9c9d6", height: 36 } : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  if (process.env.VIDIGEN_DEV_URL) win.loadURL(process.env.VIDIGEN_DEV_URL);
  else win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
}

ipcMain.handle("engine:info", async () => {
  if (!engineReady) startEngine();
  return engineReady;
});

ipcMain.handle("dialog:open", async (_e, kind) => {
  const filters = {
    audio: [{ name: "Audio", extensions: ["mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"] }],
    video: [{ name: "Video", extensions: ["mp4", "mov", "mkv", "webm", "avi"] }],
  }[kind];
  const res = await dialog.showOpenDialog(win, { properties: ["openFile"], filters });
  return res.canceled ? null : res.filePaths[0];
});

ipcMain.handle("shell:reveal", (_e, p) => shell.showItemInFolder(p));

ipcMain.handle("file:saveAs", async (_e, src) => {
  const res = await dialog.showSaveDialog(win, {
    defaultPath: path.basename(src),
    filters: [{ name: "MP4 video", extensions: ["mp4"] }],
  });
  if (res.canceled || !res.filePath) return null;
  await fs.promises.copyFile(src, res.filePath);
  return res.filePath;
});

app.whenReady().then(() => {
  // media://local/<encoded path> streams local files to <audio>/<video> elements.
  protocol.handle("media", (req) => {
    const p = decodeURIComponent(new URL(req.url).pathname.slice(1));
    return net.fetch(pathToFileURL(p).toString(), { headers: req.headers });
  });
  startEngine().catch((e) => console.error(e.message));
  createWindow();
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (engine) engine.kill();
});
