import { useEffect, useState } from "react";
import { api, bridge, fmtTime, Job } from "../api";

const STAGES = [
  ["analyze", "Beat"],
  ["plan", "Shots"],
  ["keyframes", "Keyframes"],
  ["motion", "Motion"],
  ["assemble", "Edit"],
  ["mux", "Mix"],
] as const;

export function JobView({ job }: { job: Job | null }) {
  const [video, setVideo] = useState<string | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setVideo(null);
    if (job?.status === "done") api.fileUrl(job.id, "video").then(setVideo);
  }, [job?.id, job?.status]);

  // While rendering, show the newest keyframe so the user sees the video take shape.
  const kfIndex = job?.stage === "keyframes" ? Math.max(0, parseInt(job.message.split(" ")[1] ?? "1") - 2) : -1;
  useEffect(() => {
    if (job && kfIndex >= 0) api.fileUrl(job.id, "keyframe", kfIndex).then((u) => u && setFrame(u));
  }, [job?.id, kfIndex]);

  if (!job) {
    return (
      <div className="phone empty">
        <div className="empty-inner">
          <div className="empty-icon">▶</div>
          <p>Your video shows up here</p>
          <small>1080 × 1920 · 30 fps · H.264</small>
        </div>
      </div>
    );
  }

  const stageIdx = STAGES.findIndex(([s]) => s === job.stage);
  const running = job.status === "running" || job.status === "queued";

  return (
    <div className="jobview">
      <div className="phone">
        {video ? (
          <video src={video} controls autoPlay loop playsInline />
        ) : frame ? (
          <img src={frame} className="pending-frame" alt="" />
        ) : (
          <div className="phone-glow" />
        )}
        {running && (
          <div className="phone-overlay">
            <div className="ring" style={{ ["--p" as string]: job.progress }}>
              <span>{Math.round(job.progress * 100)}%</span>
            </div>
            <p>{job.message || "Queued"}</p>
          </div>
        )}
      </div>

      <div className="stages">
        {STAGES.map(([key, label], i) => (
          <div
            key={key}
            className={`stage ${job.status === "done" || i < stageIdx ? "done" : i === stageIdx && running ? "active" : ""}`}
          >
            <i />
            {label}
          </div>
        ))}
      </div>

      {job.status === "failed" && <div className="error">{job.error}</div>}

      {job.shots.length > 0 && (
        <div className="shots">
          <div className="shots-head">
            <span>
              {job.shots.length} shots · {job.window.tempo?.toFixed(0)} BPM
            </span>
            <span className="muted">planned by {job.planner === "llm" ? "local LLM" : "templates"}</span>
          </div>
          <div className="shot-bar">
            {job.shots.map((s) => (
              <div
                key={s.index}
                className="shot-seg"
                style={{ flexGrow: s.duration, opacity: 0.45 + s.intensity * 0.55 }}
                title={`${fmtTime(s.start)} · ${s.motion.replace("_", " ")}\n${s.prompt}`}
              />
            ))}
          </div>
        </div>
      )}

      {job.status === "done" && (
        <div className="actions">
          <div className="caption">
            <label>Suggested caption</label>
            <pre>{job.caption}</pre>
          </div>
          <div className="row">
            <button
              className="ghost"
              onClick={() => {
                navigator.clipboard.writeText(job.caption);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? "Copied" : "Copy caption"}
            </button>
            {bridge && (
              <>
                <button className="ghost" onClick={() => bridge?.reveal(job.output)}>
                  Show in folder
                </button>
                <button onClick={() => bridge?.saveAs(job.output)}>Export MP4</button>
              </>
            )}
          </div>
        </div>
      )}
      {running && (
        <button className="ghost danger" onClick={() => api.cancel(job.id)}>
          Cancel render
        </button>
      )}
    </div>
  );
}
