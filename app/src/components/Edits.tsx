import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, AutopilotConfig, AutopilotState, bridge, EditItem, EditRequest, engineUrl, fmtTime, LibraryOverview, TrackItem,
} from "../api";

const EFFECT_LABELS: Record<string, string> = {
  flash: "Flash",
  punch: "Zoom punch",
  shake: "Shake",
  rgb: "RGB split",
  slowmo: "Slow-mo build-up",
};

const DEFAULT_REQ: EditRequest = {
  track_id: null,
  categories: [],
  duration: 20,
  intensity: "hard",
  look: "auto",
  effects: ["flash", "punch", "shake", "rgb", "slowmo"],
  hook: "",
  allow_nc: true,
};

const active = (e: EditItem) => e.status === "queued" || e.status === "running";

function dayLabel(day: string) {
  const d = new Date(`${day}T12:00:00`);
  const today = new Date();
  const diff = Math.round((new Date(today.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function nextRunLabel(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (d <= now) return "starting now";
  if (d.toDateString() === now.toDateString()) return `today at ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `tomorrow at ${time}`;
  return `${d.toLocaleDateString(undefined, { weekday: "long" })} at ${time}`;
}

export function Edits({ overview, onOpenLibrary }: { overview: LibraryOverview | null; onOpenLibrary: () => void }) {
  const [edits, setEdits] = useState<EditItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [req, setReq] = useState<EditRequest>(DEFAULT_REQ);
  const [count, setCount] = useState(1);
  const [tracks, setTracks] = useState<TrackItem[]>([]);
  const [err, setErr] = useState("");
  const [pilot, setPilot] = useState<AutopilotState | null>(null);
  const [cfg, setCfg] = useState<AutopilotConfig | null>(null);
  const [pilotErr, setPilotErr] = useState("");
  const lastFinished = useRef<number | undefined>(undefined);

  const load = useCallback(() => api.edits().then(setEdits).catch(() => {}), []);
  const loadPilot = useCallback(
    () =>
      api
        .autopilot()
        .then((p) => {
          setPilot(p);
          setCfg((c) => c ?? p.config);
          // Tell the user when a scheduled batch lands.
          const fin = p.last_result.finished;
          const made = p.last_result.edits?.length ?? 0;
          if (lastFinished.current !== undefined && fin && fin !== lastFinished.current && made > 0) {
            try {
              new Notification("Vidigen", { body: `${made} new edit${made > 1 ? "s are" : " is"} ready to post` });
            } catch {
              /* notifications unavailable */
            }
            load();
          }
          lastFinished.current = fin ?? 0;
        })
        .catch(() => {}),
    [load],
  );

  useEffect(() => {
    load();
    loadPilot();
  }, [load, loadPilot]);

  useEffect(() => {
    api.tracks().then(setTracks).catch(() => {});
  }, [overview?.stats.tracks]);

  const working = edits.some(active) || !!pilot?.running;
  useEffect(() => {
    const t = setInterval(
      () => {
        load();
        loadPilot();
      },
      working ? 1500 : 20000,
    );
    return () => clearInterval(t);
  }, [working, load, loadPilot]);

  const groups = useMemo(() => {
    const out: [string, EditItem[]][] = [];
    for (const e of edits) {
      const last = out[out.length - 1];
      if (last && last[0] === e.day) last[1].push(e);
      else out.push([e.day, [e]]);
    }
    return out;
  }, [edits]);

  const sel = edits.find((e) => e.id === selected) ?? null;
  const stats = overview?.stats;
  const empty = !!stats && (stats.tracks === 0 || stats.clips === 0);
  const cats = overview ? Object.entries(overview.categories) : [];
  const stocked = (c: string) => (stats?.per_category[c] ?? 0) > 0;

  async function make() {
    setErr("");
    try {
      let first: EditItem | null = null;
      for (let i = 0; i < count; i++) {
        const e = await api.createEdit(req);
        first = first ?? e;
      }
      if (first) setSelected(first.id);
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function savePilot(next: AutopilotConfig) {
    setCfg(next);
    setPilotErr("");
    try {
      const p = await api.saveAutopilot(next);
      setPilot(p);
      setCfg(p.config);
      if (p.error) setPilotErr(p.error);
    } catch (e) {
      setPilotErr((e as Error).message);
    }
  }

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <main className="edits">
      <section className="panel form">
        {empty && (
          <div className="card callout">
            <h2>Fill your library first</h2>
            <p className="hint">
              Edits are cut from your music and footage library. You have {stats?.tracks} tracks and {stats?.clips} clips.
              Add free API keys to download phonk and stock footage, or import folders you already have.
            </p>
            <button className="primary-sm" onClick={onOpenLibrary}>
              Open Library
            </button>
          </div>
        )}

        <div className="card">
          <h2>
            <b>1</b> Theme
          </h2>
          <div className="chips">
            <button className={`chip ${req.categories.length === 0 ? "on" : ""}`} onClick={() => setReq({ ...req, categories: [] })}>
              Auto
            </button>
            {cats.map(([k, c]) => (
              <button
                key={k}
                className={`chip ${req.categories.includes(k) ? "on" : ""}`}
                disabled={!stocked(k)}
                title={stocked(k) ? `${stats?.per_category[k]} clips` : "No clips yet"}
                onClick={() => setReq({ ...req, categories: toggle(req.categories, k) })}
              >
                {c.label}
                {stocked(k) && <span className="chip-n">{stats?.per_category[k]}</span>}
              </button>
            ))}
          </div>
          <label>
            Track
            <select
              value={req.track_id ?? ""}
              onChange={(e) => setReq({ ...req, track_id: e.target.value || null })}
            >
              <option value="">Auto: least used track</option>
              {tracks
                .filter((t) => !t.missing)
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                    {t.artist ? ` · ${t.artist}` : ""} · {Math.round(t.tempo)} BPM{t.used ? ` · used ${t.used}×` : ""}
                  </option>
                ))}
            </select>
          </label>
          <label>
            <span>
              Hook text <span className="optional-inline">optional, shown until the drop</span>
            </span>
            <input
              placeholder="pov: you hear this at 3am on the highway"
              maxLength={120}
              value={req.hook}
              onChange={(e) => setReq({ ...req, hook: e.target.value })}
            />
          </label>
        </div>

        <div className="card">
          <h2>
            <b>2</b> Cut
          </h2>
          <label>
            Length <em>{fmtTime(req.duration)}</em>
            <input
              type="range"
              min={8}
              max={60}
              step={1}
              value={req.duration}
              onChange={(e) => setReq({ ...req, duration: +e.target.value })}
            />
          </label>
          <div className="seg">
            {Object.entries(overview?.intensity ?? {}).map(([k, label]) => (
              <button
                key={k}
                className={req.intensity === k ? "on" : ""}
                onClick={() => setReq({ ...req, intensity: k as EditRequest["intensity"] })}
              >
                {label}
                <small>{k === "chill" ? "every bar" : k === "hard" ? "every 2 beats" : "every beat"}</small>
              </button>
            ))}
          </div>
          <div className="chips">
            <span className="chips-label">Look</span>
            {[["auto", "Auto"], ...Object.entries(overview?.looks ?? {})].map(([k, label]) => (
              <button key={k} className={`chip ${req.look === k ? "on" : ""}`} onClick={() => setReq({ ...req, look: k })}>
                {label}
              </button>
            ))}
          </div>
          <div className="chips">
            <span className="chips-label">Effects</span>
            {Object.entries(EFFECT_LABELS).map(([k, label]) => (
              <button
                key={k}
                className={`chip ${req.effects.includes(k) ? "on" : ""}`}
                onClick={() => setReq({ ...req, effects: toggle(req.effects, k) })}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="check">
            <input type="checkbox" checked={req.allow_nc} onChange={(e) => setReq({ ...req, allow_nc: e.target.checked })} />
            Allow non-commercial (NC) tracks
          </label>
        </div>

        {err && <div className="error">{err}</div>}
        <div className="make-row">
          <select value={count} onChange={(e) => setCount(+e.target.value)} title="How many edits to make">
            {[1, 2, 3, 5, 10].map((n) => (
              <option key={n} value={n}>
                ×{n}
              </option>
            ))}
          </select>
          <button className="primary" disabled={!overview || empty} onClick={make}>
            {count > 1 ? `Make ${count} edits` : "Make edit"}
          </button>
        </div>

        {cfg && pilot && (
          <div className={`card autopilot ${cfg.enabled ? "live" : ""}`}>
            <div className="ap-head">
              <h2>Daily autopilot</h2>
              <label className="switch">
                <input type="checkbox" checked={cfg.enabled} onChange={(e) => savePilot({ ...cfg, enabled: e.target.checked })} />
                <span />
              </label>
            </div>
            <p className="hint">
              {pilot.running
                ? pilot.message || "Working…"
                : cfg.enabled
                  ? `Next batch ${nextRunLabel(pilot.next_run)}.${pilot.last_result.day ? ` Last run: ${pilot.message || `${pilot.last_result.edits?.length ?? 0} edits`}.` : ""}`
                  : "Every day it downloads fresh footage and music, then makes a batch of edits for you to post."}
            </p>
            <div className="ap-grid">
              <label>
                Edits per day
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={cfg.per_day}
                  onChange={(e) => setCfg({ ...cfg, per_day: Math.max(1, Math.min(20, +e.target.value)) })}
                  onBlur={() => savePilot(cfg)}
                />
              </label>
              <label>
                At
                <input type="time" value={cfg.at} onChange={(e) => e.target.value && savePilot({ ...cfg, at: e.target.value })} />
              </label>
              <label>
                Length (s)
                <input
                  type="number"
                  min={8}
                  max={90}
                  value={cfg.duration}
                  onChange={(e) => setCfg({ ...cfg, duration: +e.target.value })}
                  onBlur={() => savePilot({ ...cfg, duration: Math.max(8, Math.min(90, cfg.duration)) })}
                />
              </label>
              <label>
                Intensity
                <select value={cfg.intensity} onChange={(e) => savePilot({ ...cfg, intensity: e.target.value })}>
                  {Object.entries(overview?.intensity ?? {}).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="chips">
              <span className="chips-label">Themes</span>
              <button
                className={`chip ${cfg.categories.length === 0 ? "on" : ""}`}
                onClick={() => savePilot({ ...cfg, categories: [] })}
              >
                Rotate all
              </button>
              {cats.map(([k, c]) => (
                <button
                  key={k}
                  className={`chip ${cfg.categories.includes(k) ? "on" : ""}`}
                  onClick={() => savePilot({ ...cfg, categories: toggle(cfg.categories, k) })}
                >
                  {c.label}
                </button>
              ))}
            </div>
            <label className="check">
              <input type="checkbox" checked={cfg.refresh} onChange={(e) => savePilot({ ...cfg, refresh: e.target.checked })} />
              Download {cfg.clips_per_day} new clips and {cfg.tracks_per_day} tracks first
            </label>
            {pilot.background_supported && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={cfg.background}
                  onChange={(e) => savePilot({ ...cfg, background: e.target.checked })}
                />
                Run even when Vidigen is closed
              </label>
            )}
            <label>
              <span>
                Hook lines <span className="optional-inline">one per line, picked at random; empty means none</span>
              </span>
              <textarea
                rows={2}
                value={cfg.hooks.join("\n")}
                onChange={(e) => setCfg({ ...cfg, hooks: e.target.value.split("\n") })}
                onBlur={() => savePilot({ ...cfg, hooks: cfg.hooks.map((h) => h.trim()).filter(Boolean) })}
              />
            </label>
            {pilotErr && <div className="error">{pilotErr}</div>}
            {(pilot.last_result.errors?.length ?? 0) > 0 && !pilot.running && (
              <details className="ap-errors">
                <summary>{pilot.last_result.errors!.length} problems in the last run</summary>
                <ul>
                  {pilot.last_result.errors!.slice(0, 8).map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </details>
            )}
            <div className="row">
              {pilot.running ? (
                <button className="ghost small danger" onClick={() => api.stopAutopilot().then(setPilot)}>
                  Stop
                </button>
              ) : (
                <button
                  className="ghost small"
                  disabled={empty && !overview?.keys.pexels.set && !overview?.keys.pixabay.set}
                  onClick={() => api.runAutopilot().then(setPilot).catch((e) => setPilotErr(e.message))}
                >
                  Run today's batch now
                </button>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="gallery">
        {edits.length === 0 ? (
          <div className="gallery-empty">
            <div className="empty-icon">▶</div>
            <p>Your edits show up here</p>
            <small>1080 × 1920 · cut to the beat · ready for TikTok</small>
          </div>
        ) : (
          groups.map(([day, list]) => (
            <div key={day} className="day">
              <h3>
                {dayLabel(day)} <span className="muted">{list.length}</span>
              </h3>
              <div className="tiles">
                {list.map((e) => (
                  <button key={e.id} className={`tile ${selected === e.id ? "on" : ""} ${e.status}`} onClick={() => setSelected(e.id)}>
                    {e.status === "done" ? (
                      <img src={engineUrl(`/edits/${e.id}/files/thumb`)} alt="" loading="lazy" />
                    ) : (
                      <div className="tile-state">
                        {active(e) ? (
                          <div className="ring sm" style={{ ["--p" as string]: e.progress }}>
                            <span>{Math.round(e.progress * 100)}%</span>
                          </div>
                        ) : (
                          <span>{e.status === "failed" ? "Failed" : "Cancelled"}</span>
                        )}
                      </div>
                    )}
                    <div className="tile-meta">
                      <strong>{e.track.title ?? "…"}</strong>
                      <span>
                        {e.theme.map((t) => overview?.categories[t]?.label ?? t).join(" + ")}
                        {e.request.auto && <i className="auto">auto</i>}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </section>

      {sel && (
        <EditDetail
          key={sel.id}
          edit={sel}
          categoryLabel={(c) => overview?.categories[c]?.label ?? c}
          onClose={() => setSelected(null)}
          onDelete={async () => {
            await api.deleteEdit(sel.id);
            setSelected(null);
            load();
          }}
          onAgain={() => {
            setReq({ ...DEFAULT_REQ, ...sel.request, track_id: null });
          }}
        />
      )}
    </main>
  );
}

function EditDetail({
  edit, categoryLabel, onClose, onDelete, onAgain,
}: {
  edit: EditItem;
  categoryLabel: (c: string) => string;
  onClose: () => void;
  onDelete: () => void;
  onAgain: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const running = active(edit);
  const total = edit.shots.reduce((a, s) => a + s.duration, 0) || 1;

  return (
    <aside className="detail">
      <div className="detail-head">
        <div>
          <strong>{edit.track.title ?? "New edit"}</strong>
          <span className="muted">
            {[edit.track.artist, edit.track.tempo ? `${Math.round(edit.track.tempo)} BPM` : "", edit.day]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
        <button className="ghost small" onClick={onClose} title="Close">
          ✕
        </button>
      </div>
      <div className="phone">
        {edit.status === "done" ? (
          <video src={engineUrl(`/edits/${edit.id}/files/video`)} controls autoPlay loop playsInline />
        ) : (
          <div className="phone-glow" />
        )}
        {running && (
          <div className="phone-overlay">
            <div className="ring" style={{ ["--p" as string]: edit.progress }}>
              <span>{Math.round(edit.progress * 100)}%</span>
            </div>
            <p>{edit.message || "Queued"}</p>
          </div>
        )}
      </div>
      {edit.status === "failed" && <div className="error">{edit.error}</div>}

      {edit.shots.length > 0 && (
        <div className="shots">
          <div className="shots-head">
            <span>
              {edit.shots.length} cuts · {edit.theme.map(categoryLabel).join(" + ")} · {edit.look}
            </span>
            <span className="muted">drop at {edit.drop.toFixed(1)}s</span>
          </div>
          <div className="shot-bar">
            {edit.shots.map((s, i) => (
              <div
                key={i}
                className={`shot-seg ${s.fx.includes("flash") ? "flash" : ""} ${s.speed < 1 ? "slow" : ""}`}
                style={{ flexGrow: s.duration / total }}
                title={`${s.start.toFixed(2)}s · ${s.duration.toFixed(2)}s${s.speed < 1 ? " · slow-mo" : ""}${s.fx.length ? ` · ${s.fx.join(", ")}` : ""}`}
              />
            ))}
          </div>
        </div>
      )}

      {edit.status === "done" && (
        <div className="actions">
          <div className="caption">
            <label>Caption</label>
            <pre>{edit.caption}</pre>
          </div>
          {edit.track.non_commercial && (
            <p className="hint warn">
              This track is licensed for non-commercial use only. Don't use it in paid promotions or sponsored posts.
            </p>
          )}
          <div className="row">
            <button
              className="ghost"
              onClick={() => {
                navigator.clipboard.writeText(edit.caption);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? "Copied" : "Copy caption"}
            </button>
            {bridge && (
              <>
                <button className="ghost" onClick={() => bridge?.reveal(edit.output)}>
                  Show in folder
                </button>
                <button onClick={() => bridge?.saveAs(edit.output)}>Export MP4</button>
              </>
            )}
          </div>
          <details className="credits">
            <summary>Credits</summary>
            <ul>
              {edit.credits.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
      <div className="row">
        {running ? (
          <button className="ghost small danger" onClick={() => api.cancelEdit(edit.id)}>
            Cancel
          </button>
        ) : (
          <>
            <button className="ghost small" onClick={onAgain}>
              Use these settings
            </button>
            <button className="ghost small danger" onClick={onDelete}>
              Delete
            </button>
          </>
        )}
      </div>
    </aside>
  );
}
