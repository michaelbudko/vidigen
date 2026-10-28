const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("vidigen", {
  engine: () => ipcRenderer.invoke("engine:info"),
  onEngineStatus: (cb) => {
    const h = (_e, msg) => cb(msg);
    ipcRenderer.on("engine:status", h);
    return () => ipcRenderer.removeListener("engine:status", h);
  },
  engineStatus: () => ipcRenderer.invoke("engine:status"),
  restartEngine: () => ipcRenderer.invoke("engine:restart"),
  installPython: () => ipcRenderer.invoke("setup:installPython"),
  openExternal: (url) => ipcRenderer.invoke("shell:open", url),
  openFile: (kind) => ipcRenderer.invoke("dialog:open", kind),
  reveal: (p) => ipcRenderer.invoke("shell:reveal", p),
  saveAs: (p) => ipcRenderer.invoke("file:saveAs", p),
  // Dropped File objects no longer expose .path; ask Electron for it.
  pathForFile: (file) => webUtils.getPathForFile(file),
});
