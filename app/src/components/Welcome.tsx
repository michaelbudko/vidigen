import { useEffect, useState } from "react";
import { api, bridge, EngineStatus, PYTHON_MISSING, SetupTask, SystemInfo } from "../api";

interface Props {
  sys: SystemInfo | null;
  engineErr: string;
  engineStatus: EngineStatus | null;
  onRetry: () => void;
  onDone: (goTo?: "characters") => void;
  onClose?: () => void;
}

const LLM_GB = 5;

/** First-run setup: get the engine running, scan the PC, pick and install a model preset. */
export function Welcome({ sys, engineErr, engineStatus, onRetry, onDone, onClose }: Props) {
  const [preset, setPreset] = useState<string>("");
  const [withLlm, setWithLlm] = useState(true);
  const [task, setTask] = useState<SetupTask | null>(null);
  const [installingPy, setInstallingPy] = useState(false);
  const [pyMsg, setPyMsg] = useState("");
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    if (sys && !preset) {
      setPreset(sys.hardware_preset);
      setWithLlm(!!sys.llm.recommended);
    }
  }, [sys, preset]);

  // Pick up a setup that is already running (e.g. after reopening this screen).
  useEffect(() => {
    if (sys) api.setupStatus().then((t) => t?.status === "running" && setTask(t)).catch(() => {});
  }, [sys]);

  useEffect(() => {
    if (task?.status !== "running") return;
    const t = setInterval(() => api.setupStatus().then((s) => s && setTask(s)), 1000);
    return () => clearInterval(t);
  }, [task?.status]);

  async function installPython() {
    if (!bridge) return;
    setInstallingPy(true);
    setPyMsg("");
    const r = await bridge.installPython();
    setInstallingPy(false);
    if (r.ok) {
      await bridge.restartEngine().catch(() => {});
      onRetry();
    } else if (r.opened) {
      setPyMsg("Automatic install didn't work, so the Python download page is open. Install it, then press Retry.");
    }
  }

  const header = (
    <div className="welcome-head">
      <span className="logo big" />
      <div>
        <h1>Welcome to Vidigen</h1>
        <p>Let's get your PC ready for AI videos. Everything runs locally. Phonk edits don't need any of this.</p>
      </div>
      {onClose && sys?.setup.complete && (
        <button className="ghost small close" onClick={onClose}>
          Close
        </button>
      )}
    </div>
  );

  // 1. Engine not running yet.
  if (!sys) {
    const missing = engineErr.startsWith(PYTHON_MISSING);
    const working = !engineErr || installingPy;
    return (
      <div className="welcome">
        <div className="welcome-card">
          {header}
          <ol className="wsteps">
            <li className={missing && !installingPy ? "failed" : working ? "running" : engineErr ? "failed" : "done"}>
              <i />
              <div>
                <strong>Python and the Vidigen engine</strong>
                {installingPy ? (
                  <span>{engineStatus?.message ?? "Installing Python 3.12…"}</span>
                ) : missing ? (
                  <>
                    <span>Python 3.10 or newer isn't installed. Vidigen can install Python 3.12 for you (about 30 MB).</span>
                    <div className="row">
                      <button onClick={installPython} disabled={!bridge}>
                        Install Python for me
                      </button>
                      <button className="ghost" onClick={() => bridge?.openExternal("https://www.python.org/downloads/windows/")}>
                        Download it myself
                      </button>
                      <button className="ghost" onClick={onRetry}>
                        Retry
                      </button>
                    </div>
                    {pyMsg && <p className="hint warn">{pyMsg}</p>}
                  </>
                ) : engineErr ? (
                  <>
                    <span className="err-text">{engineErr}</span>
                    <div className="row">
                      <button className="ghost" onClick={onRetry}>
                        Retry
                      </button>
                    </div>
                  </>
                ) : (
                  <span>{engineStatus?.message ?? "Starting…"} (the first launch takes about a minute)</span>
                )}
              </div>
            </li>
            <li className="pending">
              <i />
              <div>
                <strong>Scan your PC</strong>
              </div>
            </li>
            <li className="pending">
              <i />
              <div>
                <strong>Install the AI models</strong>
              </div>
            </li>
          </ol>
        </div>
      </div>
    );
  }

  const gpu = sys.gpus[0];
  const chosen = sys.presets.find((p) => p.name === preset);
  const needGb = (chosen && !chosen.downloaded ? chosen.download_gb : 0) + (withLlm ? LLM_GB : 0);
  const lowDisk = sys.disk_free_gb < needGb + 5;

  // 3. Installing.
  if (task) {
    const done = task.status === "done";
    return (
      <div className="welcome">
        <div className="welcome-card">
          {header}
          <ol className="wsteps">
            {task.steps.map((s) => (
              <li key={s.key} className={s.status}>
                <i />
                <div>
                  <strong>{s.label}</strong>
                  {s.status === "skipped" ? (
                    <span>Not needed</span>
                  ) : (
                    <>
                      {s.detail && <span>{s.detail}</span>}
                      {s.status === "running" && (
                        <div className={`bar-track ${s.progress == null ? "indeterminate" : ""}`}>
                          <div style={{ width: `${Math.round((s.progress ?? 0.3) * 100)}%` }} />
                        </div>
                      )}
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {task.status === "failed" && <div className="error">{task.error}</div>}
          <button className="link" onClick={() => setShowLog(!showLog)}>
            {showLog ? "Hide details" : "Show details"}
          </button>
          {showLog && <pre className="log">{task.log.join("\n") || "No output yet."}</pre>}
          <div className="row end">
            {task.status === "failed" && (
              <>
                <button className="ghost" onClick={() => api.skipSetup().then(() => onDone())}>
                  Use Test mode for now
                </button>
                <button onClick={() => setTask(null)}>Back</button>
              </>
            )}
            {done && (
              <>
                <button className="ghost" onClick={() => onDone()}>
                  Start creating
                </button>
                <button className="primary-sm" onClick={() => onDone("characters")}>
                  Create your character
                </button>
              </>
            )}
            {task.status === "running" && <span className="muted">You can leave this open; downloads resume if interrupted.</span>}
          </div>
        </div>
      </div>
    );
  }

  // 2. Scan results and preset choice.
  return (
    <div className="welcome">
      <div className="welcome-card">
        {header}
        <h3>Your PC</h3>
        <div className="specs">
          <div className={gpu ? "good" : "warn"}>
            <label>Graphics</label>
            <strong>{gpu ? gpu.name : "No NVIDIA GPU found"}</strong>
            <span>{gpu ? `${gpu.vram_gb} GB VRAM · driver ${gpu.driver}` : "AI video needs an NVIDIA card"}</span>
          </div>
          <div>
            <label>Processor</label>
            <strong>{sys.cpu.name}</strong>
            <span>{sys.cpu.cores} threads</span>
          </div>
          <div>
            <label>Memory</label>
            <strong>{sys.ram_gb ? `${sys.ram_gb} GB` : "Unknown"}</strong>
            <span>{sys.os}</span>
          </div>
          <div className={lowDisk ? "warn" : ""}>
            <label>Free disk</label>
            <strong>{sys.disk_free_gb} GB</strong>
            <span>{lowDisk ? `Needs about ${needGb + 5} GB` : `Python ${sys.python}`}</span>
          </div>
        </div>

        <h3>Pick a model setup</h3>
        <div className="wpresets">
          {sys.presets.map((p) => {
            const fits = (gpu?.vram_gb ?? 0) >= p.min_vram_gb;
            return (
              <button key={p.name} className={`wpreset ${preset === p.name ? "on" : ""}`} onClick={() => setPreset(p.name)}>
                <div className="wpreset-top">
                  <strong>{p.label}</strong>
                  {p.name === sys.hardware_preset && <span className="tag">Recommended</span>}
                  {p.downloaded && p.download_gb > 0 && <span className="tag ok">Installed</span>}
                </div>
                <span className="desc">{p.description}</span>
                <span className="meta">
                  {p.min_vram_gb ? `${p.min_vram_gb}+ GB VRAM` : "Any PC"} · {p.download_gb ? `~${p.download_gb} GB download` : "no download"}
                  {!fits && p.min_vram_gb > 0 && <em> · may not fit your GPU</em>}
                </span>
              </button>
            );
          })}
        </div>

        <label className="check">
          <input type="checkbox" checked={withLlm} onChange={(e) => setWithLlm(e.target.checked)} />
          Also install a local "director" AI that writes each shot ({sys.llm.recommended ?? "qwen3:4b"}, ~{LLM_GB} GB)
          {sys.llm.available && <span className="tag ok">Installed</span>}
        </label>

        <div className="row end">
          <button className="ghost" onClick={() => api.skipSetup().then(() => onDone())}>
            Skip, use Test mode
          </button>
          <button
            className="primary-sm"
            disabled={!chosen}
            onClick={async () =>
              setTask(await api.setup(preset, withLlm ? sys.llm.recommended ?? "qwen3:4b" : null))
            }
          >
            {needGb > 0 ? `Set up ${chosen?.label} (~${needGb} GB)` : `Use ${chosen?.label}`}
          </button>
        </div>
      </div>
    </div>
  );
}
