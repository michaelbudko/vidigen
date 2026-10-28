import { useCallback, useEffect, useRef, useState } from "react";
import { api, bridge, ClipItem, engineUrl, fmtTime, KeyName, LibraryOverview, LibTask, TrackItem } from "../api";

const KEYS: { name: KeyName; label: string; what: string }[] = [
  { name: "pexels", label: "Pexels", what: "stock footage" },
  { name: "pixabay", label: "Pixabay", what: "stock footage" },
  { name: "jamendo", label: "Jamendo", what: "CC phonk · client ID" },
];

// Licences for imported music. The credit line uses the label.
const MUSIC_LICENSES: [string, string][] = [
  ["ncs", "NCS (credit the artist)"],
  ["pixabay", "Pixabay Content License"],
  ["cc0", "CC0"],
  ["cc-by", "CC BY"],
  ["cc-by-sa", "CC BY-SA"],
  ["cc-by-nc", "CC BY-NC (non-commercial)"],
  ["cc-by-nc-sa", "CC BY-NC-SA (non-commercial)"],
  ["own", "My own music"],
  ["licensed", "I have a licence"],
  ["", "Unknown"],
];

function licenseBadge(t: TrackItem) {
  const l = t.license || "unknown";
  return <span className={`lic ${t.non_commercial ? "nc" : l === "unknown" ? "unknown" : ""}`}>{l.toUpperCase()}</span>;
}

export function Library({ overview, onChange }: { overview: LibraryOverview | null; onChange: () => void }) {
  const [tab, setTab] = useState<"footage" | "music">("footage");
  const [keyInputs, setKeyInputs] = useState<Partial<Record<KeyName, string>>>({});
  const [keyTest, setKeyTest] = useState<Record<string, { ok: boolean; error: string }> | null>(null);
  const [savingKeys, setSavingKeys] = useState(false);

  const [fill, setFill] = useState({ clips: 30, tracks: 10, categories: [] as string[], max_footage_gb: 40 });
  const [task, setTask] = useState<LibTask | null>(null);
  const [err, setErr] = useState("");
  const [musicLicense, setMusicLicense] = useState("ncs");
  const [footageCat, setFootageCat] = useState("drift");

  const [clips, setClips] = useState<ClipItem[]>([]);
  const [tracks, setTracks] = useState<TrackItem[]>([]);
  const [filter, setFilter] = useState("");
  const [playing, setPlaying] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);

  const loadMedia = useCallback(() => {
    api.clips(filter).then(setClips).catch(() => {});
    api.tracks().then(setTracks).catch(() => {});
  }, [filter]);

  useEffect(loadMedia, [loadMedia]);

  // Follow the running download or import.
  useEffect(() => {
    if (!task || task.status !== "running") return;
    const t = setInterval(async () => {
      const next = await api.libTask(task.id).catch(() => null);
      if (!next) return;
      setTask(next);
      if (next.status !== "running") {
        loadMedia();
        onChange();
      }
    }, 1000);
    return () => clearInterval(t);
  }, [task?.id, task?.status, loadMedia, onChange]);

  async function saveKeys() {
    setSavingKeys(true);
    setKeyTest(null);
    try {
      const changed = Object.fromEntries(Object.entries(keyInputs).filter(([, v]) => v && v.trim()));
      if (Object.keys(changed).length) await api.saveKeys(changed);
      setKeyInputs({});
      setKeyTest(await api.testKeys());
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSavingKeys(false);
    }
  }

  async function start(p: Promise<LibTask>) {
    setErr("");
    try {
      setTask(await p);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function importFolder(kind: "music" | "footage") {
    const folder = bridge ? await bridge.openFolder() : window.prompt("Folder path");
    if (!folder) return;
    start(api.importFolder({ folder, kind, license: musicLicense, category: footageCat }));
  }

  function play(t: TrackItem) {
    const a = audio.current;
    if (!a) return;
    if (playing === t.id) {
      a.pause();
      setPlaying(null);
      return;
    }
    a.src = engineUrl(`/library/track/${t.id}/file`);
    a.currentTime = 0;
    a.play().then(() => {
      // Start a little before the drop, where the track is most recognisable.
      a.currentTime = Math.max(0, t.drop_at - 4);
    });
    setPlaying(t.id);
  }

  const stats = overview?.stats;
  const keys = overview?.keys;
  const cats = overview ? Object.entries(overview.categories) : [];
  const running = task?.status === "running";
  const anyFootageKey = keys?.pexels.set || keys?.pixabay.set;

  return (
    <main className="libview">
      <section className="panel form">
        <div className="card">
          <h2>
            <b>1</b> Sources
          </h2>
          <p className="hint">
            Free keys let Vidigen download footage and music that may be reused in your posts. Each takes a minute to get.
          </p>
          {KEYS.map((k) => (
            <div key={k.name} className="keyrow">
              <div className="keyrow-top">
                <strong>{k.label}</strong>
                <span className="muted">{k.what}</span>
                {keyTest?.[k.name] && keys?.[k.name].set ? (
                  <span className={`tag ${keyTest[k.name].ok ? "ok" : "bad"}`}>
                    {keyTest[k.name].ok ? "works" : keyTest[k.name].error}
                  </span>
                ) : (
                  keys?.[k.name].set && <span className="tag ok">saved</span>
                )}
                <button
                  className="link"
                  onClick={() =>
                    bridge ? bridge.openExternal(overview!.key_links[k.name]) : window.open(overview?.key_links[k.name])
                  }
                >
                  Get a key
                </button>
              </div>
              <input
                type="password"
                autoComplete="off"
                placeholder={keys?.[k.name].set ? `Saved (${keys[k.name].hint}). Paste to replace` : "Paste the key here"}
                value={keyInputs[k.name] ?? ""}
                onChange={(e) => setKeyInputs({ ...keyInputs, [k.name]: e.target.value })}
              />
            </div>
          ))}
          <div className="row end">
            <button className="ghost small" disabled={savingKeys} onClick={saveKeys}>
              {savingKeys ? "Checking…" : "Save and test keys"}
            </button>
          </div>
        </div>

        <div className="card">
          <h2>
            <b>2</b> Download
          </h2>
          <div className="row">
            <label className="grow">
              New clips
              <input type="number" min={0} max={500} value={fill.clips} onChange={(e) => setFill({ ...fill, clips: +e.target.value })} />
            </label>
            <label className="grow">
              New tracks
              <input type="number" min={0} max={200} value={fill.tracks} onChange={(e) => setFill({ ...fill, tracks: +e.target.value })} />
            </label>
            <label className="grow">
              Footage limit (GB)
              <input
                type="number"
                min={1}
                value={fill.max_footage_gb}
                onChange={(e) => setFill({ ...fill, max_footage_gb: +e.target.value })}
              />
            </label>
          </div>
          <div className="chips">
            <button className={`chip ${fill.categories.length === 0 ? "on" : ""}`} onClick={() => setFill({ ...fill, categories: [] })}>
              All themes
            </button>
            {cats.map(([k, c]) => (
              <button
                key={k}
                className={`chip ${fill.categories.includes(k) ? "on" : ""}`}
                title={c.queries.join(", ")}
                onClick={() =>
                  setFill({
                    ...fill,
                    categories: fill.categories.includes(k) ? fill.categories.filter((x) => x !== k) : [...fill.categories, k],
                  })
                }
              >
                {c.label}
              </button>
            ))}
          </div>
          {!anyFootageKey && !keys?.jamendo.set && <p className="hint warn">Add at least one key above first.</p>}
          <button
            className="primary-sm"
            disabled={running || !(anyFootageKey || keys?.jamendo.set)}
            onClick={() => start(api.refreshLibrary(fill))}
          >
            Download to the library
          </button>
        </div>

        <div className="card">
          <h2>
            <b>3</b> Import <span className="optional">your own folders</span>
          </h2>
          <p className="hint">
            Files stay where they are. Good free phonk packs: NCS, Pixabay Music and Uppbeat. Only import music you're allowed
            to post.
          </p>
          <div className="row">
            <select value={musicLicense} onChange={(e) => setMusicLicense(e.target.value)}>
              {MUSIC_LICENSES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
            <button className="ghost small" disabled={running} onClick={() => importFolder("music")}>
              + Music folder
            </button>
          </div>
          <div className="row">
            <select value={footageCat} onChange={(e) => setFootageCat(e.target.value)}>
              {cats.map(([k, c]) => (
                <option key={k} value={k}>
                  {c.label}
                </option>
              ))}
            </select>
            <button className="ghost small" disabled={running} onClick={() => importFolder("footage")}>
              + Footage folder
            </button>
          </div>
        </div>

        {err && <div className="error">{err}</div>}
        {task && (
          <div className={`card task ${task.status}`}>
            <div className="row between">
              <strong>{task.kind === "refresh" ? "Downloading" : "Importing"}</strong>
              {running && (
                <button className="ghost small danger" onClick={() => api.cancelTask(task.id).then(setTask)}>
                  Stop
                </button>
              )}
            </div>
            <div className={`bar-track ${running && task.progress === 0 ? "indeterminate" : ""}`}>
              <div style={{ width: `${Math.round((task.status === "done" ? 1 : task.progress) * 100)}%` }} />
            </div>
            <span className="hint">
              {task.status === "running"
                ? task.message
                : task.status === "failed"
                  ? task.error
                  : task.kind === "refresh"
                    ? `Added ${task.result?.clips_added ?? 0} clips and ${task.result?.tracks_added ?? 0} tracks.${task.result?.stopped ? ` Stopped: ${task.result.stopped}.` : ""}`
                    : `Added ${task.result?.added ?? 0} of ${task.result?.found ?? 0} files${task.result?.skipped ? ` (${task.result.skipped} already there)` : ""}.`}
            </span>
            {(task.result?.errors?.length ?? 0) > 0 && (
              <details>
                <summary>{task.result!.errors!.length} skipped</summary>
                <ul className="errlist">
                  {task.result!.errors!.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </section>

      <section className="libmain">
        <div className="lib-tabs">
          <button className={tab === "footage" ? "on" : ""} onClick={() => setTab("footage")}>
            Footage <span className="muted">{stats?.clips ?? 0}</span>
          </button>
          <button className={tab === "music" ? "on" : ""} onClick={() => setTab("music")}>
            Music <span className="muted">{stats?.tracks ?? 0}</span>
          </button>
          <span className="muted lib-size">{stats ? `${stats.disk_gb} GB downloaded` : ""}</span>
          {bridge && overview && (
            <button className="ghost small" onClick={() => bridge?.reveal(overview.root)}>
              Open folder
            </button>
          )}
        </div>

        {tab === "footage" ? (
          <>
            <div className="chips">
              <button className={`chip ${filter === "" ? "on" : ""}`} onClick={() => setFilter("")}>
                All
              </button>
              {cats.map(([k, c]) => (
                <button key={k} className={`chip ${filter === k ? "on" : ""}`} onClick={() => setFilter(k)}>
                  {c.label} <span className="chip-n">{stats?.per_category[k] ?? 0}</span>
                </button>
              ))}
            </div>
            {clips.length === 0 ? (
              <p className="hint lib-empty">No footage here yet. Download some or import a folder.</p>
            ) : (
              <div className="clip-grid">
                {clips.map((c) => (
                  <div
                    key={c.id}
                    className={`clip ${c.missing ? "missing" : ""}`}
                    onMouseEnter={() => setHover(c.id)}
                    onMouseLeave={() => setHover(null)}
                  >
                    {hover === c.id && !c.missing ? (
                      <video src={engineUrl(`/library/clip/${c.id}/file`)} autoPlay muted loop playsInline />
                    ) : (
                      <img src={engineUrl(`/library/clip/${c.id}/file?what=thumb`)} alt="" loading="lazy" />
                    )}
                    <div className="clip-meta">
                      <span>{fmtTime(c.duration)}</span>
                      <span className="motion" title={`Motion ${Math.round(c.motion_mean * 100)}%`}>
                        <i style={{ width: `${Math.min(100, c.motion_mean * 160)}%` }} />
                      </span>
                      <span>{c.source}</span>
                    </div>
                    <button
                      className="clip-del"
                      title="Remove from the library"
                      onClick={() => api.removeMedia("clip", c.id).then(() => (loadMedia(), onChange()))}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : tracks.length === 0 ? (
          <p className="hint lib-empty">No music yet. Add a Jamendo client ID or import a folder of phonk.</p>
        ) : (
          <div className="tracks">
            <audio ref={audio} onEnded={() => setPlaying(null)} />
            {tracks.map((t) => (
              <div key={t.id} className={`track ${t.missing ? "missing" : ""}`}>
                <button className="play" disabled={t.missing} onClick={() => play(t)}>
                  {playing === t.id ? "❚❚" : "▶"}
                </button>
                <div className="track-name">
                  <strong>{t.title}</strong>
                  <span className="muted">{t.artist || t.source}</span>
                </div>
                <span className="muted num">{Math.round(t.tempo)} BPM</span>
                <span className="muted num" title="Where the drop is">
                  drop {fmtTime(t.drop_at)}
                </span>
                <span className="muted num">{fmtTime(t.duration)}</span>
                {t.source === "local" ? (
                  <select
                    className="lic-select"
                    value={t.license}
                    onChange={(e) => api.updateTrack(t.id, { license: e.target.value }).then(loadMedia)}
                  >
                    {MUSIC_LICENSES.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                ) : (
                  licenseBadge(t)
                )}
                <span className="muted num">{t.used ? `used ${t.used}×` : "new"}</span>
                <div className="track-actions">
                  {t.page_url && (
                    <button className="ghost small" onClick={() => bridge?.openExternal(t.page_url)} title={t.page_url}>
                      Source
                    </button>
                  )}
                  <button
                    className="ghost small danger"
                    onClick={() => api.removeMedia("track", t.id).then(() => (loadMedia(), onChange()))}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
