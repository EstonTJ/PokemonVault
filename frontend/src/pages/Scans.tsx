import { Link } from "react-router-dom";
import { api, Scan } from "../api";
import { DemoTag, Empty, ErrorBox, PageHeader, Spinner, StatusPill, useAsync } from "../ui";

export default function Scans() {
  const { data, error, loading, reload } = useAsync(() => api<Scan[]>("/api/scans"), []);

  async function act(s: Scan, path: string, method = "POST", confirmMsg?: string) {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    try { await api(`/api/scans/${s.id}${path}`, { method }); reload(); } catch (e: any) { alert(e.message); }
  }

  return (
    <div>
      <PageHeader title="Scan history" subtitle="Each recording you've uploaded and what's still stored from it." />
      {loading && !data && <div className="flex justify-center py-16"><Spinner /></div>}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data?.length === 0 && <Empty>No scans yet.</Empty>}
      <div className="space-y-3">
        {data?.map((s) => (
          <div key={s.id} className="card p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Link to={`/review/scan/${s.id}`} className="flex items-center gap-2 truncate font-semibold">{s.original_filename} {s.is_demo && <DemoTag />}</Link>
                <div className="text-xs text-slate-500">
                  {new Date(s.created_at).toLocaleString()} · {(s.size_bytes / 1048576).toFixed(1)} MB
                  {s.duration_s ? ` · ${s.duration_s.toFixed(0)}s` : ""} · {s.summary?.groups ?? 0} items
                </div>
              </div>
              <StatusPill status={s.status} />
            </div>
            {s.job && ["queued", "running"].includes(s.job.status) && <div className="mt-2 text-xs text-slate-500">{s.job.stage} · {Math.round(s.job.progress * 100)}%</div>}
            {s.error && <div className="mt-2"><ErrorBox error={s.error} /></div>}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className={`chip ${s.video_stored ? "bg-amber-500/15 text-amber-700 dark:text-amber-300" : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"}`}>
                {s.video_stored ? "Raw video stored" : "Raw video deleted"}
              </span>
              <span className="chip bg-slate-500/15">{s.frames_retained} evidence frames</span>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {s.job && ["queued", "running"].includes(s.job.status) && <button className="btn-ghost px-3 py-1.5" onClick={() => act(s, "/cancel")}>Cancel</button>}
              {["failed", "cancelled"].includes(s.status) && s.video_stored && <button className="btn-ghost px-3 py-1.5" onClick={() => act(s, "/retry")}>Retry</button>}
              {s.video_stored && <button className="btn-ghost px-3 py-1.5" onClick={() => act(s, "/discard-video", "POST", "Delete the stored raw video?")}>Delete video</button>}
              {s.frames_retained > 0 && <button className="btn-ghost px-3 py-1.5" onClick={() => act(s, "/purge-frames", "POST", "Delete evidence frames? Confirmed Pokémon stay, but you lose the screenshots behind them.")}>Purge frames</button>}
              <button className="btn-ghost px-3 py-1.5 text-rose-600" onClick={() => act(s, "", "DELETE", "Delete this scan and its pending items? Confirmed Pokémon stay in your vault.")}>Delete scan</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
