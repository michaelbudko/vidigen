import { useEffect, useMemo, useState } from "react";
import { api, bridge, Character, characterFile, Task } from "../api";

const STYLE_LABELS: Record<string, string> = {
  realistic: "Realistic",
  cartoon: "Cartoon",
  anime: "Anime",
  "3d": "3D animated",
  meme: "Meme",
};

export function avatarOf(c: Character) {
  const file = c.look ?? c.photos.find((p) => p.enabled)?.face ?? c.photos[0]?.face;
  return file ? characterFile(c.id, file, c.updated) : "";
}

interface Props {
  characters: Character[];
  selected: string | null;
  preset: string;
  presetReady: boolean;
  onSelect: (id: string | null) => void;
  onChange: () => void;
  onOpenSetup: () => void;
}

export function Characters({ characters, selected, preset, presetReady, onSelect, onChange, onOpenSetup }: Props) {
  const current = characters.find((c) => c.id === selected) ?? null;
  return (
    <div className="chars">
      <aside className="chars-list">
        <button className={`char-item new ${current ? "" : "on"}`} onClick={() => onSelect(null)}>
          <span className="avatar plus">+</span>
          <span>New character</span>
        </button>
        {characters.map((c) => (
          <button key={c.id} className={`char-item ${c.id === selected ? "on" : ""}`} onClick={() => onSelect(c.id)}>
            <img className="avatar" src={avatarOf(c)} alt="" />
            <span>
              <strong>{c.name}</strong>
              <small>
                {c.photos.length} photo{c.photos.length === 1 ? "" : "s"}
                {c.look ? " · look pinned" : ""}
              </small>
            </span>
          </button>
        ))}
      </aside>
      <section className="chars-main">
        {current ? (
          <Editor
            key={current.id}
            c={current}
            preset={preset}
            presetReady={presetReady}
            onChange={onChange}
            onDeleted={() => {
              onSelect(null);
              onChange();
            }}
            onOpenSetup={onOpenSetup}
          />
        ) : (
          <NewCharacter
            onCreated={(c) => {
              onChange();
              onSelect(c.id);
            }}
          />
        )}
      </section>
    </div>
  );
}

function usePhotoPicker(onPick: (paths: string[]) => void) {
  return {
    pick: async () => {
      if (bridge) onPick(await bridge.openFile("images"));
      else {
        const p = window.prompt("Photo paths, comma separated");
        if (p) onPick(p.split(",").map((s) => s.trim()).filter(Boolean));
      }
    },
    dropProps: {
      onDragOver: (e: React.DragEvent) => e.preventDefault(),
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        const b = bridge;
        if (b) onPick(Array.from(e.dataTransfer.files).map((f) => b.pathForFile(f)));
      },
    },
  };
}

const basename = (p: string) => p.split(/[\\/]/).pop() ?? p;

function NewCharacter({ onCreated }: { onCreated: (c: Character) => void }) {
  const [name, setName] = useState("Me");
  const [photos, setPhotos] = useState<string[]>([]);
  const [description, setDescription] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const picker = usePhotoPicker((p) => setPhotos((old) => [...new Set([...old, ...p])]));

  async function create() {
    setBusy(true);
    setErr("");
    try {
      onCreated(await api.createCharacter({ name, photos, description, style: "realistic", consent }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="editor">
      <h2 className="editor-title">Create a character</h2>
      <p className="hint">
        Upload 3–6 clear photos of yourself: face visible, good light, a few different angles. Vidigen learns your face from
        them and keeps it consistent in every shot.
      </p>
      <div className="card">
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
        </label>
        <div className="drop" onClick={picker.pick} {...picker.dropProps}>
          <strong>{photos.length ? `${photos.length} photo${photos.length > 1 ? "s" : ""} selected` : "Add photos"}</strong>
          <span>{photos.length ? photos.map(basename).join(", ") : "JPG, PNG, WEBP or HEIC · click or drop"}</span>
        </div>
        <label>
          Describe yourself <span className="muted">(helps the AI with hair, age and build)</span>
          <textarea
            rows={2}
            value={description}
            placeholder="e.g. 28-year-old man, short dark brown hair, light stubble, athletic build"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          These photos are of me, or of someone who has given me permission to use their likeness.
        </label>
        {err && <div className="error">{err}</div>}
        <button disabled={!photos.length || !consent || !name.trim() || busy} onClick={create}>
          {busy ? "Reading photos…" : "Create character"}
        </button>
      </div>
    </div>
  );
}

function Editor({
  c,
  preset,
  presetReady,
  onChange,
  onDeleted,
  onOpenSetup,
}: {
  c: Character;
  preset: string;
  presetReady: boolean;
  onChange: () => void;
  onDeleted: () => void;
  onOpenSetup: () => void;
}) {
  const [name, setName] = useState(c.name);
  const [description, setDescription] = useState(c.description);
  const [note, setNote] = useState("");
  const [likeness, setLikeness] = useState(c.likeness);
  const [task, setTask] = useState<Task | null>(null);
  const [err, setErr] = useState("");

  const save = async (patch: Parameters<typeof api.updateCharacter>[1]) => {
    setErr("");
    try {
      await api.updateCharacter(c.id, patch);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const picker = usePhotoPicker(async (paths) => {
    if (!paths.length) return;
    try {
      await api.addPhotos(c.id, paths);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    }
  });

  useEffect(() => {
    if (!task || task.status === "done" || task.status === "failed") return;
    const t = setInterval(async () => {
      const s = await api.task(task.id);
      setTask(s);
      if (s.status === "done") onChange();
      if (s.status === "failed") setErr(s.error);
    }, 700);
    return () => clearInterval(t);
  }, [task?.id, task?.status]);

  const batches = useMemo(() => {
    const by = new Map<number, Character["previews"]>();
    for (const p of c.previews) by.set(p.batch, [...(by.get(p.batch) ?? []), p]);
    return [...by.entries()].sort((a, b) => b[0] - a[0]);
  }, [c.previews]);

  const generating = task && (task.status === "queued" || task.status === "running");

  async function generate() {
    setErr("");
    // Save pending text edits first so the previews use them.
    const patch: Parameters<typeof api.updateCharacter>[1] = {};
    if (description !== c.description) patch.description = description;
    if (likeness !== c.likeness) patch.likeness = likeness;
    if (Object.keys(patch).length) await api.updateCharacter(c.id, patch);
    try {
      setTask(await api.previews(c.id, preset, 4));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <div className="editor">
      <div className="editor-head">
        {c.look ? <img className="avatar xl" src={characterFile(c.id, c.look, c.updated)} alt="" /> : null}
        <input
          className="name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== c.name && save({ name: name.trim() })}
        />
        <button
          className="ghost small danger"
          onClick={async () => {
            if (window.confirm(`Delete ${c.name} and its photos?`)) {
              await api.deleteCharacter(c.id);
              onDeleted();
            }
          }}
        >
          Delete
        </button>
      </div>

      <div className="editor-grid">
        <div className="card">
          <h2>
            <b>1</b> Photos
          </h2>
          <div className="photo-grid">
            {c.photos.map((p) => (
              <div key={p.file} className={`photo ${p.enabled ? "" : "off"}`}>
                <img src={characterFile(c.id, p.face)} alt="" />
                <button
                  className="photo-toggle"
                  title={p.enabled ? "Used as a reference. Click to leave it out." : "Not used. Click to use it."}
                  onClick={() => save({ photo_enabled: { [p.file]: !p.enabled } })}
                >
                  {p.enabled ? "✓" : ""}
                </button>
                <button
                  className="photo-remove"
                  title="Remove photo"
                  onClick={() => api.removePhoto(c.id, p.file).then(onChange)}
                >
                  ×
                </button>
              </div>
            ))}
            <button className="photo add" onClick={picker.pick} {...picker.dropProps}>
              + Add
            </button>
          </div>
          <p className="hint">Untick blurry or odd-angle photos; they can pull the likeness off.</p>

          <h2>
            <b>2</b> Description
          </h2>
          <textarea
            rows={2}
            value={description}
            placeholder="e.g. 28-year-old man, short dark brown hair, light stubble"
            onChange={(e) => setDescription(e.target.value)}
            onBlur={() => description !== c.description && save({ description })}
          />

          <h2>
            <b>3</b> Revisions
          </h2>
          <div className="notes">
            {c.notes.map((n, i) => (
              <span key={i} className="note">
                {n}
                <button onClick={() => save({ notes: c.notes.filter((_, j) => j !== i) })}>×</button>
              </span>
            ))}
            {!c.notes.length && <span className="muted">Nothing yet. Generate previews, then say what to fix.</span>}
          </div>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) save({ notes: [...c.notes, note.trim()] }).then(() => setNote(""));
            }}
          >
            <input
              placeholder="What should change? e.g. hair a bit shorter, rounder face"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="ghost small" type="submit">
              Add
            </button>
          </form>

          <label>
            Likeness <em>{Math.round(likeness * 100)}%</em>
            <input
              type="range"
              min={0.2}
              max={1}
              step={0.05}
              value={likeness}
              onChange={(e) => setLikeness(+e.target.value)}
              onMouseUp={() => likeness !== c.likeness && save({ likeness })}
              onKeyUp={() => likeness !== c.likeness && save({ likeness })}
            />
            <span className="hint">Higher looks more like your photos; lower follows the scene and style more.</span>
          </label>

          <div className="chips">
            {Object.entries(STYLE_LABELS).map(([k, label]) => (
              <button key={k} className={`chip ${c.style === k ? "on" : ""}`} onClick={() => save({ style: k })}>
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="card previews-card">
          <div className="row between">
            <h2>Previews</h2>
            <button className="primary-sm" disabled={!!generating || !c.photos.some((p) => p.enabled)} onClick={generate}>
              {generating ? task?.message || "Working…" : "Generate 4 previews"}
            </button>
          </div>
          {!presetReady && (
            <p className="hint warn">
              The AI models aren't installed, so previews use Test mode (your photo on abstract art).{" "}
              <button className="link" onClick={onOpenSetup}>
                Open Setup
              </button>
            </p>
          )}
          {generating && (
            <div className="bar-track">
              <div style={{ width: `${Math.max(5, task!.progress * 100)}%` }} />
            </div>
          )}
          {err && <div className="error">{err}</div>}
          {!batches.length && !generating && (
            <p className="muted">Generate previews to see how Vidigen draws {c.name}. Pin the one that looks most like you.</p>
          )}
          {batches.map(([batch, items]) => (
            <div key={batch} className="batch">
              <small className="muted">
                Round {batch} · {STYLE_LABELS[items[0].style] ?? items[0].style}
              </small>
              <div className="pv-grid">
                {items.map((p) => (
                  <div key={p.file} className={`pv ${c.look === p.file ? "pinned" : ""}`}>
                    <img src={characterFile(c.id, p.file)} alt="" />
                    <button
                      className={c.look === p.file ? "pin on" : "pin"}
                      onClick={() => save(c.look === p.file ? { clear_look: true } : { look: p.file })}
                    >
                      {c.look === p.file ? "★ This is me" : "☆ Looks like me"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {c.look && (
            <label className="check">
              <input
                type="checkbox"
                checked={c.look_as_reference}
                onChange={(e) => save({ look_as_reference: e.target.checked })}
              />
              Use the pinned look as an extra reference in videos
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
