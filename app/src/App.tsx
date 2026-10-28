import { useCallback, useEffect, useRef, useState } from "react";
import {
  Analysis, api, bridge, Character, EngineStatus, fmtTime, Job, LibraryOverview, mediaUrl, resetConnection, SongLicense,
  SystemInfo,
} from "./api";
import { avatarOf, Characters } from "./components/Characters";
import { Edits } from "./components/Edits";
import { EnergyTimeline } from "./components/EnergyTimeline";
import { JobView } from "./components/JobView";
import { Library } from "./components/Library";
import { Welcome } from "./components/Welcome";

type View = "edits" | "library" | "create" | "characters";

const LICENSES: { value: SongLicense; label: string; credit: boolean }[] = [
  { value: "own_work", label: "My own music", credit: false },
  { value: "cc0", label: "CC0 (no rights reserved)", credit: false },
  { value: "public_domain", label: "Public domain", credit: false },
  { value: "cc_by", label: "CC BY (credit the artist)", credit: true },
  { value: "cc_by_sa", label: "CC BY-SA (credit the artist)", credit: true },
  { value: "licensed", label: "I have a licence for it", credit: false },
];

const STYLES = ["Cinematic", "Anime", "Vaporwave", "Claymation", "Film noir", "Cyberpunk", "Watercolor", "Lo-fi VHS"];

const basename = (p: string) => p.split(/[\\/]/).pop() ?? p;

export default function App() {
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [engineErr, setEngineErr] = useState("");
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [view, setView] = useState<View>("edits");
  const [showSetup, setShowSetup] = useState(false);
  const [overview, setOverview] = useState<LibraryOverview | null>(null);

  const [characters, setCharacters] = useState<Character[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [cast, setCast] = useState<string[]>([]);

  const [prompt, setPrompt] = useState("");
  const [style, setStyle] = useState("Cinematic");

  const [song, setSong] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(45);
  const [bpm, setBpm] = useState(120);

  const [license, setLicense] = useState<SongLicense>("own_work");
  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");

  const [footage, setFootage] = useState<string | null>(null);
  const [preset, setPreset] = useState("mock");
  const [useLlm, setUseLlm] = useState(true);
  const [seed, setSeed] = useState(42);

  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [formErr, setFormErr] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);

  const loadCharacters = useCallback(() => api.characters().then(setCharacters).catch(() => {}), []);
  const loadOverview = useCallback(() => api.library().then(setOverview).catch(() => {}), []);

  const connect = useCallback(() => {
    setEngineErr("");
    setSys(null);
    resetConnection();
    api
      .system()
      .then((s) => {
        setSys(s);
        setPreset(s.recommended_preset);
        setUseLlm(s.llm.available);
        api.jobs().then(setHistory).catch(() => {});
        loadCharacters();
        loadOverview();
      })
      .catch((e) => setEngineErr(String(e.message ?? e)));
  }, [loadCharacters, loadOverview]);

  // The autopilot can add to the library at any time.
  useEffect(() => {
    if (!sys) return;
    const t = setInterval(loadOverview, 30000);
    return () => clearInterval(t);
  }, [sys, loadOverview]);

  useEffect(() => {
    const off = bridge?.onEngineStatus(setEngineStatus);
    bridge?.engineStatus().then(setEngineStatus);
    connect();
    return off;
  }, [connect]);

  // Drop cast members that were deleted.
  useEffect(() => {
    setCast((c) => c.filter((id) => characters.some((ch) => ch.id === id)));
  }, [characters]);

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
        setSong(null);
      } finally {
        setAnalyzing(false);
      }
    },
    [duration],
  );

  function clearSong() {
    audioRef.current?.pause();
    setSong(null);
    setAnalysis(null);
  }

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

  const needsCredit = LICENSES.find((l) => l.value === license)!.credit && !(title && artist);
  const busy = job?.status === "running" || job?.status === "queued";
  const ready = !!sys && prompt.trim().length >= 3 && !analyzing;
  const presetInfo = sys?.presets.find((p) => p.name === preset);
  const presetReady = !!presetInfo && (presetInfo.download_gb === 0 || (presetInfo.downloaded && !!sys?.ml_runtime));
  // Phonk edits need no AI models, so the setup screen only gates the AI tabs.
  const aiTab = view === "create" || view === "characters";
  const welcome = !sys || showSetup || (!sys.setup.complete && aiTab);
  const maxLen = analysis ? Math.min(90, Math.floor(analysis.duration)) : 90;

  async function render() {
    setFormErr("");
    try {
      const j = await api.render({
        song_path: song,
        characters: cast,
        bpm,
        prompt,
        style: style.toLowerCase(),
        duration,
        start: song ? start : null,
        preset,
        use_llm: useLlm,
        seed,
        source_video: footage,
        rights: { license, title, artist, source_url: sourceUrl },
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

  async function pickSong() {
    const p = bridge ? await bridge.openFile("audio") : window.prompt("Path to audio file");
    if (p) loadSong(p);
  }

  const gpu = sys?.gpus[0];

  return (
    <div className="app">
      <header className="titlebar">
        <div className="brand">
          <span className="logo" />
          Vidigen
        </div>
        <nav className="tabs">
          <button className={view === "edits" ? "on" : ""} onClick={() => setView("edits")}>
            Edits
          </button>
          <button className={view === "library" ? "on" : ""} onClick={() => setView("library")}>
            Library
          </button>
          <span className="tabs-sep" />
          <button className={view === "create" ? "on" : ""} onClick={() => setView("create")}>
            AI video
          </button>
          <button className={view === "characters" ? "on" : ""} onClick={() => setView("characters")}>
            Characters {characters.length > 0 && <span className="count">{characters.length}</span>}
          </button>
        </nav>
        <div className="status">
          {engineErr ? (
            <span className="pill bad">Engine offline</span>
          ) : sys ? (
            <>
              {overview && (
                <span className="pill">
                  {overview.stats.tracks} tracks · {overview.stats.clips} clips
                </span>
              )}
              <span className={`pill ${gpu ? "good" : ""}`}>
                {gpu ? `${gpu.name} · ${gpu.vram_gb} GB` : "No GPU detected"}
              </span>
              {aiTab && <span className={`pill ${sys.llm.available ? "good" : ""}`}>
                {sys.llm.available ? `LLM · ${sys.llm.model}` : "LLM offline"}
              </span>}
            </>
          ) : (
            <span className="pill">{engineStatus?.message ?? "Starting engine…"}</span>
          )}
          <button className="pill btn" onClick={() => setShowSetup(true)}>
            Setup
          </button>
        </div>
      </header>

      {welcome && (
        <Welcome
          sys={sys}
          engineErr={engineErr}
          engineStatus={engineStatus}
          onRetry={connect}
          onClose={() => setShowSetup(false)}
          onDone={(goTo) => {
            setShowSetup(false);
            connect();
            if (goTo === "characters") {
              setView("characters");
              setEditing(null);
            }
          }}
        />
      )}

      {view === "edits" && sys && <Edits overview={overview} onOpenLibrary={() => setView("library")} />}
      {view === "library" && sys && <Library overview={overview} onChange={loadOverview} />}

      {view === "characters" && sys && (
        <Characters
          characters={characters}
          selected={editing}
          preset={presetReady ? preset : "mock"}
          presetReady={presetReady}
          onSelect={setEditing}
          onChange={loadCharacters}
          onOpenSetup={() => setShowSetup(true)}
        />
      )}

      <main className="layout" hidden={view !== "create"}>
        <section className="panel form">
          <div className="card">
            <h2>
              <b>1</b> Vision
            </h2>
            <textarea
              rows={3}
              autoFocus
              placeholder="A lone astronaut wandering a neon desert at night, chasing a glowing jellyfish…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className="cast">
              <span className="cast-label">Cast</span>
              {characters.map((c) => {
                const on = cast.includes(c.id);
                return (
                  <button
                    key={c.id}
                    className={`cast-chip ${on ? "on" : ""}`}
                    onClick={() => setCast(on ? cast.filter((id) => id !== c.id) : [...cast, c.id])}
                    title={on ? "In this video" : "Add to this video"}
                  >
                    <img src={avatarOf(c)} alt="" />
                    {c.name}
                  </button>
                );
              })}
              <button
                className="cast-chip add"
                onClick={() => {
                  setView("characters");
                  setEditing(null);
                }}
              >
                {characters.length ? "+ New" : "+ Add yourself as a character"}
              </button>
            </div>
            <div className="chips">
              {STYLES.map((s) => (
                <button key={s} className={`chip ${style === s ? "on" : ""}`} onClick={() => setStyle(style === s ? "" : s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <h2>
              <b>2</b> Music <span className="optional">optional</span>
            </h2>
            {song ? (
              <div className="drop has">
                <div className="drop-row">
                  <strong>{basename(song)}</strong>
                  <button className="ghost small" onClick={clearSong}>
                    Remove
                  </button>
                </div>
                <span>
                  {analyzing
                    ? "Finding the beat…"
                    : analysis
                      ? `${analysis.tempo.toFixed(0)} BPM · ${fmtTime(analysis.duration)} · ${analysis.beats.length} beats`
                      : ""}
                </span>
              </div>
            ) : (
              <div
                className="drop"
                onClick={pickSong}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const f = e.dataTransfer.files[0];
                  if (f && bridge) loadSong(bridge.pathForFile(f));
                }}
              >
                <strong>Add a song</strong>
                <span>Or skip it: you get a silent video cut to the tempo below, ready for a TikTok sound</span>
              </div>
            )}
            {analysis && <EnergyTimeline analysis={analysis} start={start} duration={duration} onSeek={setStart} />}
            <div className="row">
              <label className="grow">
                Length <em>{fmtTime(duration)}</em>
                <input
                  type="range"
                  min={30}
                  max={maxLen}
                  step={5}
                  value={duration}
                  onChange={(e) => {
                    const d = +e.target.value;
                    setDuration(d);
                    if (analysis) setStart((s) => Math.min(s, analysis.duration - d));
                  }}
                />
              </label>
              {song ? (
                bridge && (
                  <button className="ghost small" onClick={preview}>
                    ▶ Preview
                  </button>
                )
              ) : (
                <label className="seed">
                  BPM
                  <input type="number" min={60} max={200} value={bpm} onChange={(e) => setBpm(+e.target.value)} />
                </label>
              )}
            </div>
            {song && bridge && <audio ref={audioRef} src={mediaUrl(song)} />}
            {song && (
              <div className="rights">
                <div className="row">
                  <select value={license} onChange={(e) => setLicense(e.target.value as SongLicense)}>
                    {LICENSES.map((l) => (
                      <option key={l.value} value={l.value}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                  <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
                  <input placeholder="Artist" value={artist} onChange={(e) => setArtist(e.target.value)} />
                </div>
                {needsCredit && <p className="hint warn">Add the title and artist so the caption credits them.</p>}
              </div>
            )}
          </div>

          <div className="card">
            <h2>
              <b>3</b> Footage <span className="optional">optional</span>
            </h2>
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
                <>
                  <span>Restyle your own clip instead of generating scenes</span>
                  <button
                    className="ghost small"
                    disabled={!bridge}
                    onClick={async () => setFootage((await bridge?.openFile("video")) ?? null)}
                  >
                    + Add video
                  </button>
                </>
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
                    <strong>{p.label}</strong>
                    <span>{p.min_vram_gb ? `${p.min_vram_gb}+ GB` : "any PC"}</span>
                    {p.download_gb > 0 && !p.downloaded ? (
                      <small>not installed</small>
                    ) : (
                      tooBig && p.min_vram_gb > 0 && <small>may not fit</small>
                    )}
                  </button>
                );
              })}
            </div>
            <p className="hint">{sys?.presets.find((p) => p.name === preset)?.description}</p>
            {sys && !presetReady && (
              <p className="hint warn">
                {presetInfo?.label} isn't installed yet.{" "}
                <button className="link" onClick={() => setShowSetup(true)}>
                  Open Setup
                </button>
              </p>
            )}
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
                    <span className="muted">{h.request.song_path ? basename(h.request.song_path) : "no song"}</span>
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
