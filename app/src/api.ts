export type SongLicense = "public_domain" | "cc0" | "cc_by" | "cc_by_sa" | "own_work" | "licensed";

export interface Analysis {
  duration: number;
  tempo: number;
  beats: number[];
  downbeats: number[];
  energy: number[];
}

export interface Shot {
  index: number;
  start: number;
  end: number;
  duration: number;
  prompt: string;
  motion: string;
  intensity: number;
}

export interface RenderRequest {
  song_path: string | null;
  characters: string[];
  bpm: number;
  prompt: string;
  style: string;
  duration: number;
  start: number | null;
  preset: string;
  use_llm: boolean;
  seed: number;
  source_video: string | null;
  rights: {
    license: SongLicense;
    title: string;
    artist: string;
    source_url: string;
  };
}

export interface Job {
  id: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string;
  progress: number;
  message: string;
  error: string;
  planner: string;
  shots: Shot[];
  window: { start?: number; end?: number; tempo?: number };
  output: string;
  thumbnail: string;
  caption: string;
  created: number;
  request: RenderRequest;
}

export interface Preset {
  name: string;
  label: string;
  min_vram_gb: number;
  description: string;
  image_model: string;
  video_model: string;
  download_gb: number;
  downloaded: boolean;
}

export interface SystemInfo {
  os: string;
  python: string;
  cpu: { name: string; cores: number };
  ram_gb: number | null;
  disk_free_gb: number;
  gpus: { name: string; vram_gb: number; driver: string; compute: string }[];
  ffmpeg: boolean;
  torch: { installed: boolean; version?: string; cuda?: string; cuda_available?: boolean; device?: string };
  ml_runtime: boolean;
  llm: { url: string; model: string; installed: boolean; available: boolean; recommended: string | null };
  hardware_preset: string;
  recommended_preset: string;
  presets: Preset[];
  setup: { complete?: boolean; preset?: string; skipped?: boolean; llm_model?: string };
  styles: string[];
}

export interface SetupStep {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "skipped" | "failed";
  progress: number | null;
  detail: string;
}

export interface SetupTask {
  preset: string;
  llm: string | null;
  steps: SetupStep[];
  status: "running" | "done" | "failed";
  error: string;
  log: string[];
}

export interface CharacterPhoto {
  file: string;
  face: string;
  enabled: boolean;
}

export interface CharacterPreview {
  file: string;
  seed: number;
  prompt: string;
  style: string;
  batch: number;
  created: number;
}

export interface Character {
  id: string;
  name: string;
  description: string;
  notes: string[];
  likeness: number;
  style: string;
  photos: CharacterPhoto[];
  previews: CharacterPreview[];
  look: string | null;
  look_as_reference: boolean;
  created: number;
  updated: number;
}

export interface CharacterPatch {
  name?: string;
  description?: string;
  notes?: string[];
  likeness?: number;
  style?: string;
  look?: string;
  clear_look?: boolean;
  look_as_reference?: boolean;
  photo_enabled?: Record<string, boolean>;
}

export interface Task {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  message: string;
  progress: number;
  error: string;
}

export interface EngineStatus {
  phase: "starting" | "checking" | "installing" | "python-missing" | "ready" | "error";
  message: string;
}

interface Bridge {
  engine(): Promise<{ url: string; token: string }>;
  engineStatus(): Promise<EngineStatus>;
  onEngineStatus(cb: (s: EngineStatus) => void): () => void;
  restartEngine(): Promise<{ url: string; token: string }>;
  installPython(): Promise<{ ok: boolean; opened?: boolean; error?: string }>;
  openExternal(url: string): Promise<void>;
  openFile(kind: "audio" | "video"): Promise<string | null>;
  openFile(kind: "images"): Promise<string[]>;
  reveal(path: string): Promise<void>;
  saveAs(path: string): Promise<string | null>;
  pathForFile(file: File): string;
}

declare global {
  interface Window {
    vidigen?: Bridge;
  }
}

export const bridge = window.vidigen;

let conn: Promise<{ url: string; token: string }> | null = null;
// Kept once connected so <img> tags can point straight at engine URLs.
let live: { url: string; token: string } | null = null;

export const PYTHON_MISSING = "[PYTHON_MISSING]";

export function resetConnection() {
  conn = null;
  live = null;
}

function connection() {
  if (!conn) {
    if (bridge) {
      conn = bridge.engine().then((c) => (live = c)).catch((e: Error) => {
        conn = null; // allow a retry
        throw new Error(e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, ""));
      });
    } else {
      // Plain-browser mode for UI development: ?engine=http://127.0.0.1:PORT&token=...
      const q = new URLSearchParams(location.search);
      live = { url: q.get("engine") ?? "", token: q.get("token") ?? "" };
      conn = Promise.resolve(live);
    }
  }
  return conn;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { url, token } = await connection();
  const res = await fetch(url + path, {
    ...init,
    headers: { "content-type": "application/json", "x-vidigen-token": token, ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, "")).join("; ")
      : body.detail;
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  system: () => call<SystemInfo>("/system"),
  analyze: (path: string, duration: number, start: number | null) =>
    call<{ analysis: Analysis; window: { start: number; end: number } }>("/analyze", {
      method: "POST",
      body: JSON.stringify({ path, duration, start }),
    }),
  render: (req: RenderRequest) => call<Job>("/jobs", { method: "POST", body: JSON.stringify(req) }),
  jobs: () => call<Job[]>("/jobs"),
  setup: (preset: string, llm: string | null) =>
    call<SetupTask>("/setup", { method: "POST", body: JSON.stringify({ preset, llm }) }),
  setupStatus: () => call<SetupTask | null>("/setup"),
  skipSetup: () => call<SystemInfo["setup"]>("/setup/skip", { method: "POST" }),
  characters: () => call<Character[]>("/characters"),
  character: (id: string) => call<Character>(`/characters/${id}`),
  createCharacter: (body: { name: string; photos: string[]; description: string; style: string; consent: boolean }) =>
    call<Character>("/characters", { method: "POST", body: JSON.stringify(body) }),
  updateCharacter: (id: string, patch: CharacterPatch) =>
    call<Character>(`/characters/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  addPhotos: (id: string, photos: string[]) =>
    call<Character>(`/characters/${id}/photos`, { method: "POST", body: JSON.stringify({ photos }) }),
  removePhoto: (id: string, file: string) =>
    call<Character>(`/characters/${id}/photos?file=${encodeURIComponent(file)}`, { method: "DELETE" }),
  deleteCharacter: (id: string) => call<{ ok: boolean }>(`/characters/${id}`, { method: "DELETE" }),
  previews: (id: string, preset: string, count = 4, style?: string) =>
    call<Task>(`/characters/${id}/previews`, { method: "POST", body: JSON.stringify({ preset, count, style }) }),
  task: (id: string) => call<Task>(`/tasks/${id}`),
  job: (id: string) => call<Job>(`/jobs/${id}`),
  cancel: (id: string) => call<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  // Media elements cannot send headers, so fetch with the token and hand back a blob URL.
  fileUrl: async (id: string, kind: "video" | "thumbnail" | "keyframe", index = 0) => {
    const { url, token } = await connection();
    const res = await fetch(`${url}/jobs/${id}/files/${kind}?index=${index}`, {
      headers: { "x-vidigen-token": token },
    });
    if (!res.ok) return null;
    return URL.createObjectURL(await res.blob());
  },
};

export function characterFile(id: string, path: string, bust = 0) {
  if (!live) return "";
  const q = new URLSearchParams({ path, t: live.token, v: String(bust) });
  return `${live.url}/characters/${id}/file?${q}`;
}

export function mediaUrl(path: string) {
  return bridge ? `media://local/${encodeURIComponent(path)}` : "";
}

export function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}
