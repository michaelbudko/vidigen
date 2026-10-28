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
  min_vram_gb: number;
  description: string;
  image_model: string;
  video_model: string;
}

export interface SystemInfo {
  gpus: { name: string; vram_gb: number }[];
  ffmpeg: boolean;
  ml_runtime: boolean;
  llm: { url: string; model: string; available: boolean };
  recommended_preset: string;
  presets: Preset[];
}

interface Bridge {
  engine(): Promise<{ url: string; token: string }>;
  onEngineStatus(cb: (msg: string) => void): () => void;
  openFile(kind: "audio" | "video"): Promise<string | null>;
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

function connection() {
  if (!conn) {
    if (bridge) {
      conn = bridge.engine().catch((e: Error) => {
        conn = null; // allow a retry
        throw new Error(e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, ""));
      });
    } else {
      // Plain-browser mode for UI development: ?engine=http://127.0.0.1:PORT&token=...
      const q = new URLSearchParams(location.search);
      conn = Promise.resolve({ url: q.get("engine") ?? "", token: q.get("token") ?? "" });
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

export function mediaUrl(path: string) {
  return bridge ? `media://local/${encodeURIComponent(path)}` : "";
}

export function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}
