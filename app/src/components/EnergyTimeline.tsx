import { useRef } from "react";
import { Analysis, fmtTime } from "../api";

interface Props {
  analysis: Analysis;
  start: number;
  duration: number;
  onSeek: (start: number) => void;
}

/** Song loudness over time with the selected clip window. Click to move the window. */
export function EnergyTimeline({ analysis, start, duration, onSeek }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const total = analysis.duration || 1;
  const bars = analysis.energy;

  const handle = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const t = ((e.clientX - r.left) / r.width) * total - duration / 2;
    onSeek(Math.max(0, Math.min(total - duration, t)));
  };

  return (
    <div className="timeline">
      <div className="timeline-track" ref={ref} onClick={handle} title="Click to choose the part of the song">
        <svg viewBox={`0 0 ${bars.length} 100`} preserveAspectRatio="none">
          {bars.map((e, i) => {
            const inside = i >= start && i < start + duration;
            const h = 6 + e * 90;
            return (
              <rect
                key={i}
                x={i + 0.15}
                y={100 - h}
                width={0.7}
                height={h}
                rx={0.3}
                className={inside ? "bar on" : "bar"}
              />
            );
          })}
        </svg>
        <div
          className="timeline-window"
          style={{ left: `${(start / total) * 100}%`, width: `${(duration / total) * 100}%` }}
        />
      </div>
      <div className="timeline-labels">
        <span>0:00</span>
        <span className="accent">
          {fmtTime(start)} – {fmtTime(start + duration)}
        </span>
        <span>{fmtTime(total)}</span>
      </div>
    </div>
  );
}
