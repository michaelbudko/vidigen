import { useCallback, useEffect, useRef, useState } from "react";
import { Analysis, api, bridge, fmtTime, Job, mediaUrl, SongLicense, SystemInfo } from "./api";
import { EnergyTimeline } from "./components/EnergyTimeline";
import { JobView } from "./components/JobView";

const LICENSES: { value: SongLicense; label: string; credit: boolean }[] = [
  { value: "own_work", label: "My own music", credit: false },
  { value: "cc0", label: "CC0 (no rights reserved)", credit: false },
  { value: "public_domain", label: "Public domain", credit: false },
  { value: "cc_by", label: "CC BY (credit required)", credit: true },
  { value: "cc_by_sa", label: "CC BY-SA (credit required)", credit: true },
  { value: "licensed", label: "I have a licence for it", credit: false },
];

const STYLES = ["Cinematic", "Anime", "Vaporwave", "Claymation", "Film noir", "Cyberpunk", "Watercolor", "Lo-fi VHS"];

const basename = (p: string) => p.split(/[\\/]/).pop() ?? p;

export default function App() {
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [engineErr, setEngineErr] = useState("");

  const [song, setSong] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(45);

  const [license, setLicense] = useState<SongLicense>("own_work");
  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [attested, setAttested] = useState(false);

  const [prompt, setPrompt] = useState("");
  const [style, setStyle] = useState("Cinematic");
  const [footage, setFootage] = useState<string | null>(null);
  const [preset, setPreset] = useState("mock");
  const [useLlm, setUseLlm] = useState(true);
  const [seed, setSeed] = useState(42);

  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [formErr, setFormErr] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    api
      .system()
      .then((s) => {
        setSys(s);
        setPreset(s.recommended_preset);
        setUseLlm(s.llm.available);
      })
      .catch((e) => setEngineErr(String(e.message ?? e)));
    api.jobs().then(setHistory).catch(() => {});
  }, []);

  const loadSong = useCallback(
    async (path: string) => {
      setSong(path);
      setAnalysis(null);
      setAnalyzing(true);
      setFormErr("");
      try {
        const r = await api.analyze(path, duration, null);
        setAnalysis(r.analysis);
        setStart(r.window.start);
        if (r.analysis.duration < duration) setDuration(Math.max(30, Math.floor(r.analysis.duration)));
      } catch (e) {
        setFormErr((e as Error).message);
      } finally {
        setAnalyzing(false);
      }
    },
    [duration],
  );

  // Poll the active job.
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const t = setInterval(async () => {
      const j = await api.job(job.id);
      setJob(j);
      if (!["queued", "running"].includes(j.status)) api.jobs().then(setHistory);
    }, 700);
    return () => clearInterval(t);
  }, [job?.id, job?.status]);

  const credit = LICENSES.find((l) => l.value === license)!.credit;
  const busy = job?.status === "running" || job?.status === "queued";
  const ready = song && analysis && prompt.trim().length >= 3 && attested && (!credit || (title && artist));

  async function render() {
    setFormErr("");
    try {
      const j = await api.render({
        song_path: song,
        prompt,
        style: style.toLowerCase(),
        duration,
        start,
        preset,
        use_llm: useLlm,
        seed,
        source_video: footage,
        rights: { license, title, artist, source_url: sourceUrl, attested },
      });
      setJob(j);
    } catch (e) {
      setFormErr((e as Error).message);
    }
  }

  function preview() {
    const a = audioRef.current;
    if (!a) return;
    if (!a.paused) return a.pause();
    a.currentTime = start;
    a.play();
  }

  const gpu = sys?.gpus[0];

  return (
    <div className="app">
      <header className="titlebar">
        <div className="brand">
          <span className="logo" />
          Vidigen
        </div>
        <div className="status">
          {engineErr ? (
            <span className="pill bad" title={engineErr}>
              Engine offline
            </span>
          ) : sys ? (
            <>
              <span className={`pill ${gpu ? "good" : ""}`}>
                {gpu ? `${gpu.name} · ${gpu.vram_gb} GB` : "No GPU detected"}
              </span>
              <span className={`pill ${sys.llm.available ? "good" : ""}`}>
                {sys.llm.available ? `LLM · ${sys.llm.model}` : "LLM offline"}
              </span>
            </>
          ) : (
            <span className="pill">Starting engine…</span>
          )}
        </div>
      </header>

      <main className="layout">
        <section className="panel form">
          <div className="card">
            <h2>
              <b>1</b> Song
            </h2>
            <div
              className={`drop ${song ? "has" : ""}`}
              onClick={async () => {
                const p = bridge ? await bridge.openFile("audio") : window.prompt("Path to audio file");
                if (p) loadSong(p);
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files[0];
                if (f && bridge) loadSong(bridge.pathForFile(f));
              }}
            >
              {song ? (
                <>
                  <strong>{basename(song)}</strong>
                  <span>
                    {analyzing
                      ? "Finding the beat…"
                      : analysis
                        ? `${analysis.tempo.toFixed(0)} BPM · ${fmtTime(analysis.duration)} · ${analysis.beats.length} beats`
                        : ""}
                  </span>
                </>
              ) : (
                <>
                  <strong>Drop a song or click to browse</strong>
                  <span>MP3, WAV, FLAC, OGG, M4A</span>
                </>
              )}
            </div>
            {analysis && (
              <>
                <EnergyTimeline analysis={analysis} start={start} duration={duration} onSeek={setStart} />
                <div className="row">
                  <label className="grow">
                    Length <em>{fmtTime(duration)}</em>
                    <input
                      type="range"
                      min={30}
                      max={Math.min(90, Math.floor(analysis.duration))}
                      step={5}
                      value={duration}
                      onChange={(e) => {
                        const d = +e.target.value;
                        setDuration(d);
                        setStart((s) => Math.min(s, analysis.duration - d));
                      }}
                    />
                  </label>
                  {bridge && (
                    <button className="ghost small" onClick={preview}>
                      ▶ Preview
                    </button>
                  )}
                </div>
                {bridge && <audio ref={audioRef} src={mediaUrl(song)} />}
              </>
            )}
          </div>

          <div className="card">
            <h2>
              <b>2</b> Rights
            </h2>
            <select value={license} onChange={(e) => setLicense(e.target.value as SongLicense)}>
              {LICENSES.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
            <div className="row">
              <input placeholder={`Song title${credit ? "" : " (optional)"}`} value={title} onChange={(e) => setTitle(e.target.value)} />
              <input placeholder={`Artist${credit ? "" : " (optional)"}`} value={artist} onChange={(e) => setArtist(e.target.value)} />
            </div>
            {credit && <input placeholder="Source link (optional)" value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} />}
            <label className="check">
              <input type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />I confirm I
              have the right to use this song in a public video.
            </label>
          </div>

          <div className="card">
            <h2>
              <b>3</b> Vision
            </h2>
            <textarea
              rows={3}
              placeholder="A lone astronaut wandering a neon desert at night, chasing a glowing jellyfish…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className="chips">
              {STYLES.map((s) => (
                <button key={s} className={`chip ${style === s ? "on" : ""}`} onClick={() => setStyle(style === s ? "" : s)}>
                  {s}
                </button>
              ))}
            </div>
            <div className="footage">
              {footage ? (
                <>
                  <span>
                    Restyling <strong>{basename(footage)}</strong>
                  </span>
                  <button className="ghost small" onClick={() => setFootage(null)}>
                    Remove
                  </button>
                </>
              ) : (
                <button
                  className="ghost small"
                  disabled={!bridge}
                  onClick={async () => setFootage((await bridge?.openFile("video")) ?? null)}
                >
                  + Add your own footage (video-to-video)
                </button>
              )}
            </div>
          </div>

          <div className="card">
            <h2>
              <b>4</b> Engine
            </h2>
            <div className="presets">
              {sys?.presets.map((p) => {
                const tooBig = (gpu?.vram_gb ?? 0) < p.min_vram_gb;
                return (
                  <button
                    key={p.name}
                    className={`preset ${preset === p.name ? "on" : ""}`}
                    onClick={() => setPreset(p.name)}
                    title={p.description}
                  >
                    <strong>{p.name}</strong>
                    <span>{p.min_vram_gb ? `${p.min_vram_gb}+ GB` : "any PC"}</span>
                    {tooBig && p.min_vram_gb > 0 && <small>may not fit</small>}
                  </button>
                );
              })}
            </div>
            <p className="hint">{sys?.presets.find((p) => p.name === preset)?.description}</p>
            <div className="row">
              <label className="check">
                <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
                Direct shots with the local LLM
              </label>
              <label className="seed">
                Seed
                <input type="number" value={seed} onChange={(e) => setSeed(+e.target.value)} />
              </label>
            </div>
          </div>

          {formErr && <div className="error">{formErr}</div>}
          <button className="primary" disabled={!ready || busy} onClick={render}>
            {busy ? "Rendering…" : "Generate music video"}
          </button>
        </section>

        <section className="panel preview">
          <JobView job={job} />
          {history.length > 0 && (
            <div className="history">
              <h3>Recent</h3>
              <div className="history-list">
                {history.map((h) => (
                  <button key={h.id} className={`history-item ${job?.id === h.id ? "on" : ""}`} onClick={() => setJob(h)}>
                    <span className={`dot ${h.status}`} />
                    <span className="h-prompt">{h.request.prompt}</span>
                    <span className="muted">{basename(h.request.song_path)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
