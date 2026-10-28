// Electron main process: owns the window and the Python engine child process.
const { app, BrowserWindow, ipcMain, dialog, shell, protocol, net } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
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

// The renderer's Welcome screen looks for this prefix to offer a one-click install.
const PYTHON_MISSING = "[PYTHON_MISSING] Vidigen needs Python 3.10 or newer, and none was found on this PC.";

const venvPython = (dir) =>
  isWin ? path.join(dir, "Scripts", "python.exe") : path.join(dir, "bin", "python");

let lastStatus = { phase: "starting", message: "Starting engine" };
function sendStatus(phase, message) {
  lastStatus = { phase, message };
  if (win && !win.isDestroyed()) win.webContents.send("engine:status", lastStatus);
}

function check(cmd, args, code) {
  // On Windows "python" may be the Microsoft Store stub, which exits 9009; this weeds it out.
  const r = spawnSync(cmd, [...args, "-c", code], { encoding: "utf8", windowsHide: true, timeout: 20000 });
  return r.status === 0 && r.stdout.trim() === "ok";
}

function windowsPythonPaths() {
  // A fresh install is not on this process's PATH yet, so look where installers put it.
  const local = process.env.LOCALAPPDATA ?? "";
  const roots = [path.join(local, "Programs", "Python"), "C:\\Program Files", "C:\\Program Files\\Python"];
  const out = [[path.join(local, "Programs", "Python", "Launcher", "py.exe"), ["-3"]], ["C:\\Windows\\py.exe", ["-3"]]];
  for (const v of ["313", "312", "311", "310"]) {
    for (const r of roots) out.push([path.join(r, `Python${v}`, "python.exe"), []]);
  }
  return out.filter(([p]) => fs.existsSync(p));
}

function findBasePython() {
  const candidates = [];
  if (process.env.VIDIGEN_PYTHON) candidates.push([process.env.VIDIGEN_PYTHON, []]);
  if (isWin) {
    candidates.push(["py", ["-3.12"]], ["py", ["-3"]], ["python", []], ["python3", []]);
    candidates.push(...windowsPythonPaths());
  } else candidates.push(["python3", []], ["python", []]);
  const recent = "import sys; print('ok' if sys.version_info >= (3, 10) else 'old')";
  return candidates.find(([cmd, args]) => check(cmd, args, recent)) ?? null;
}

function run(cmd, args, label) {
  return new Promise((resolve, reject) => {
    let tail = "";
    const p = spawn(cmd, args, { windowsHide: true });
    const onData = (d) => {
      tail = (tail + d.toString()).slice(-3000);
      const line = d.toString().trim().split(/\r?\n/).pop();
      if (line) sendStatus("installing", `${label}: ${line.slice(0, 120)}`);
    };
    p.stdout.on("data", onData);
    p.stderr.on("data", onData);
    p.on("error", reject);
    p.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${label} failed (${code}).\n${tail}`))));
  });
}

// Use engine/.venv from setup.ps1 if it is there; otherwise build a small venv in
// the user's app data on first launch so the app runs with no manual setup.
async function ensurePython() {
  const ready = "import vidigen_engine, fastapi, uvicorn; print('ok')";
  const venvs = [path.join(engineDir, ".venv"), path.join(app.getPath("userData"), "engine-venv")];
  for (const v of venvs) {
    const py = venvPython(v);
    if (fs.existsSync(py) && check(py, [], ready)) return py;
  }
  sendStatus("checking", "Looking for Python");
  const base = findBasePython();
  if (!base) {
    sendStatus("python-missing", "Python not found");
    throw new Error(PYTHON_MISSING);
  }
  const target = venvs[1];
  const py = venvPython(target);
  if (!fs.existsSync(py)) {
    sendStatus("installing", "First launch: creating the Python environment");
    await run(base[0], [...base[1], "-m", "venv", target], "Creating environment");
  }
  sendStatus("installing", "First launch: installing the engine (about a minute)");
  const pkg = app.isPackaged ? [engineDir] : ["-e", engineDir];
  await run(py, ["-m", "pip", "install", "--disable-pip-version-check", ...pkg], "Installing engine");
  return py;
}

async function startEngine() {
  const python = await ensurePython();
  sendStatus("starting", "Starting engine");
  const token = crypto.randomBytes(24).toString("base64url");
  engine = spawn(python, ["-m", "vidigen_engine", "serve"], {
    cwd: engineDir,
    env: { ...process.env, VIDIGEN_TOKEN: token, PYTHONUNBUFFERED: "1" },
    windowsHide: true,
  });
  return new Promise((resolve, reject) => {
    let buf = "";
    let errTail = "";
    engine.stdout.on("data", (chunk) => {
      buf += chunk.toString();
      const m = buf.match(/VIDIGEN_READY port=(\d+)/);
      if (m && !engineInfo) {
        engineInfo = { url: `http://127.0.0.1:${m[1]}`, token };
        sendStatus("ready", "Engine running");
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
      else sendStatus("error", `Engine stopped (${code})`);
      engine = null;
      engineInfo = null;
      engineReady = null;
    });
  });
}

function getEngine() {
  if (!engineReady) {
    engineReady = startEngine();
    // Let a later call (the Retry button) try again after a failure.
    engineReady.catch(() => {
      engineReady = null;
    });
  }
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

ipcMain.handle("engine:info", () => getEngine());
ipcMain.handle("engine:status", () => lastStatus);

ipcMain.handle("engine:restart", () => {
  if (engine) engine.kill();
  engine = null;
  engineInfo = null;
  engineReady = null;
  return getEngine();
});

// One-click Python for the Welcome screen. Falls back to the download page if winget is missing.
ipcMain.handle("setup:installPython", async () => {
  if (!isWin) {
    shell.openExternal("https://www.python.org/downloads/");
    return { ok: false, opened: true };
  }
  sendStatus("installing", "Installing Python 3.12 (this can take a few minutes)");
  try {
    await run(
      "winget",
      ["install", "-e", "--id", "Python.Python.3.12", "--scope", "user", "--silent",
        "--accept-package-agreements", "--accept-source-agreements"],
      "Installing Python",
    );
    return { ok: true };
  } catch (e) {
    if (!findBasePython()) {
      shell.openExternal("https://www.python.org/downloads/windows/");
      return { ok: false, opened: true, error: e.message };
    }
    return { ok: true };
  }
});

ipcMain.handle("shell:open", (_e, url) => {
  if (/^https:\/\//.test(url)) shell.openExternal(url);
});

ipcMain.handle("dialog:open", async (_e, kind) => {
  const filters = {
    audio: [{ name: "Audio", extensions: ["mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"] }],
    video: [{ name: "Video", extensions: ["mp4", "mov", "mkv", "webm", "avi"] }],
    images: [{ name: "Photos", extensions: ["jpg", "jpeg", "png", "webp", "heic", "heif"] }],
  }[kind];
  const multi = kind === "images";
  const res = await dialog.showOpenDialog(win, {
    properties: multi ? ["openFile", "multiSelections"] : ["openFile"],
    filters,
  });
  if (res.canceled) return multi ? [] : null;
  return multi ? res.filePaths : res.filePaths[0];
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
  createWindow();
  getEngine().catch((e) => console.error(e.message));
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (engine) engine.kill();
});
