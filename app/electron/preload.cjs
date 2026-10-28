const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("vidigen", {
  engine: () => ipcRenderer.invoke("engine:info"),
  onEngineStatus: (cb) => {
    const h = (_e, msg) => cb(msg);
    ipcRenderer.on("engine:status", h);
    return () => ipcRenderer.removeListener("engine:status", h);
  },
  openFile: (kind) => ipcRenderer.invoke("dialog:open", kind),
  reveal: (p) => ipcRenderer.invoke("shell:reveal", p),
  saveAs: (p) => ipcRenderer.invoke("file:saveAs", p),
  // Dropped File objects no longer expose .path; ask Electron for it.
  pathForFile: (file) => webUtils.getPathForFile(file),
});
