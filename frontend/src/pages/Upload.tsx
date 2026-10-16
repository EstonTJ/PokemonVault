import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, cancelUpload, Scan, uploadResumable, UploadProgress } from "../api";
import { ErrorBox, PageHeader, ProgressBar, StatusPill } from "../ui";

type Phase = "idle" | "uploading" | "paused" | "processing" | "finished" | "duplicate" | "error";

const mb = (n: number) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(0)} MB`);
function eta(p: UploadProgress) {
  if (p.bytesPerSec < 1) return "";
  const s = (p.totalBytes - p.sentBytes) / p.bytesPerSec;
  return s < 60 ? `~${Math.max(1, Math.round(s))}s left` : `~${Math.round(s / 60)} min left`;
}

export default function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("auto");
  const [keepVideo, setKeepVideo] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [scan, setScan] = useState<Scan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [limitMb, setLimitMb] = useState<number | null>(null);
  const abortRef = useRef<AbortController>();
  const sessionRef = useRef<string>();

  useEffect(() => { api("/api/settings").then((s) => setLimitMb(s.limits.max_upload_mb)).catch(() => {}); }, []);

  // Poll the job while processing.
  useEffect(() => {
    if (phase !== "processing" || !scan) return;
    const t = window.setInterval(async () => {
      try {
        const s = await api<Scan>(`/api/scans/${scan.id}`);
        setScan(s);
        const st = s.job?.status;
        if (st === "succeeded" || st === "failed" || st === "cancelled") setPhase("finished");
      } catch (e: any) { setErr(e.message); }
    }, 1200);
    return () => window.clearInterval(t);
  }, [phase, scan?.id]);

  function choose(f: File | null) {
    setErr(null); setPhase("idle"); setScan(null);
    if (!f) return setFile(null);
    const ok = /\.(mov|mp4|m4v)$/i.test(f.name);
    if (!ok) { setErr("Choose a MOV or MP4 screen recording."); return setFile(null); }
    if (limitMb && f.size > limitMb * 1024 * 1024) { setErr(`This file is ${(f.size / 1048576).toFixed(0)} MB; the limit is ${limitMb} MB. Trim the recording and try again.`); return setFile(null); }
    setFile(f);
  }

  async function start(reprocess = false) {
    if (!file) return;
    setErr(null); setPhase("uploading"); setProgress(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const { data } = await uploadResumable(file, { kind, keep_video: keepVideo, reprocess }, setProgress, ctrl.signal,
        (id) => { sessionRef.current = id; });
      sessionRef.current = undefined;
      setScan(data.scan);
      setPhase(data.duplicate ? "duplicate" : "processing");
    } catch (e) {
      if (ctrl.signal.aborted) { setPhase((p) => (p === "idle" ? p : "paused")); return; }
      setErr((e as ApiError).message); setPhase("error");
    }
  }

  function pause() {
    abortRef.current?.abort();  // the server keeps what it has; Resume continues from there
  }

  async function cancel() {
    if (phase === "uploading" || phase === "paused" || phase === "error") {
      abortRef.current?.abort();
      if (sessionRef.current) await cancelUpload(sessionRef.current);
      sessionRef.current = undefined;
      setPhase("idle"); setProgress(null);
      return;
    }
    if (!scan) return;
    try { await api(`/api/scans/${scan.id}/cancel`, { method: "POST" }); } catch (e: any) { setErr(e.message); }
  }

  async function retry() {
    if (!scan) return;
    try {
      const s = await api<Scan>(`/api/scans/${scan.id}/retry`, { method: "POST" });
      setScan(s); setPhase("processing"); setErr(null);
    } catch (e: any) { setErr(e.message); }
  }

  const job = scan?.job;
  const summary = scan?.summary ?? {};

  return (
    <div className="mx-auto max-w-xl">
      <PageHeader title="Scan a recording" subtitle="Upload an iPhone screen recording of your Pokémon storage, detail pages or appraisals." />

      <div className="card p-5">
        <label className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-10 text-center transition ${file ? "border-vault-400 bg-vault-500/5" : "border-slate-300 dark:border-white/10"}`}>
          <input type="file" accept="video/mp4,video/quicktime,.mov,.mp4,.m4v" className="hidden"
            onChange={(e) => choose(e.target.files?.[0] ?? null)} disabled={phase === "uploading" || phase === "processing"} />
          <svg viewBox="0 0 24 24" className="mb-2 h-8 w-8 text-vault-500" fill="none" stroke="currentColor" strokeWidth={1.6}><path d="M12 16V4m0 0l-5 5m5-5l5 5M4 20h16" /></svg>
          {file ? (
            <>
              <div className="font-semibold">{file.name}</div>
              <div className="text-xs text-slate-500">{(file.size / 1048576).toFixed(1)} MB · tap to change</div>
            </>
          ) : (
            <>
              <div className="font-semibold">Choose a MOV or MP4</div>
              <div className="text-xs text-slate-500">{limitMb ? `Up to ${limitMb} MB` : ""}</div>
            </>
          )}
        </label>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <div>
            <span className="label">Recording type</span>
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="auto">Detect automatically</option>
              <option value="collection">Storage grid / details</option>
              <option value="appraisal">Appraisal</option>
            </select>
          </div>
          <div>
            <span className="label">Raw video</span>
            <button type="button" onClick={() => setKeepVideo(!keepVideo)}
              className={`input text-left ${keepVideo ? "ring-amber-400" : ""}`}>{keepVideo ? "Keep after processing" : "Delete after processing"}</button>
          </div>
        </div>

        {err && <div className="mt-4"><ErrorBox error={err} /></div>}

        {phase === "idle" || phase === "error" || phase === "paused" ? (
          <div className="mt-5 flex gap-2">
            <button className="btn-primary flex-1" disabled={!file} onClick={() => start()}>
              {phase === "idle" ? "Upload & scan" : "Resume upload"}
            </button>
            {phase !== "idle" && <button className="btn-ghost" onClick={cancel}>Cancel</button>}
          </div>
        ) : null}

        {(phase === "uploading" || phase === "paused") && progress && (
          <div className="mt-5 space-y-2">
            <div className="flex justify-between gap-2 text-xs text-slate-500">
              <span>
                {phase === "paused" ? "Paused" : progress.phase === "finishing" ? "Checking the file…"
                  : progress.phase === "waiting" ? "Connection trouble, retrying…" : "Uploading…"}
                {" "}{mb(progress.sentBytes)} of {mb(progress.totalBytes)}
              </span>
              <span className="tabular-nums">
                {phase === "uploading" && progress.phase === "sending" && progress.bytesPerSec > 0
                  ? `${mb(progress.bytesPerSec)}/s · ${eta(progress)}` : `${Math.round((progress.sentBytes / progress.totalBytes) * 100)}%`}
              </span>
            </div>
            <ProgressBar value={progress.sentBytes / progress.totalBytes} />
            {progress.resumedBytes > 0 && (
              <div className="text-xs text-emerald-600 dark:text-emerald-400">Resumed: {mb(progress.resumedBytes)} was already uploaded.</div>
            )}
            {phase === "uploading" && progress.phase !== "finishing" && (
              <div className="flex gap-2">
                <button className="btn-ghost flex-1" onClick={pause}>Pause</button>
                <button className="btn-ghost flex-1" onClick={cancel}>Cancel upload</button>
              </div>
            )}
            {phase === "paused" && (
              <p className="text-xs text-slate-500">What's uploaded is kept for 24 hours. Pick the same file to resume, even after closing this page.</p>
            )}
          </div>
        )}
        {phase === "uploading" && !progress && <div className="mt-5 text-xs text-slate-500">Starting upload…</div>}

        {(phase === "processing" || phase === "finished") && scan && (
          <div className="mt-5 space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">{job?.stage ?? "queued"}</span>
              <StatusPill status={job?.status ?? scan.status} />
            </div>
            <ProgressBar value={job?.progress ?? 0} />
            {phase === "processing" && <button className="btn-ghost w-full" onClick={cancel} disabled={job?.cancel_requested}>{job?.cancel_requested ? "Cancelling…" : "Cancel processing"}</button>}
            {phase === "finished" && job?.status === "succeeded" && (
              <div className="rounded-xl bg-emerald-500/10 p-3 text-sm ring-1 ring-emerald-500/20">
                <div className="font-semibold">Scan complete</div>
                <div className="mt-1 text-slate-600 dark:text-slate-300">
                  {summary.keyframes ?? 0} key frames analysed · {summary.groups ?? 0} Pokémon found · {summary.needs_review ?? 0} need review
                  {summary.relinked ? ` · ${summary.relinked} re-linked to existing` : ""}
                  {summary.auto_linked ? ` · ${summary.auto_linked} recognised as already in your vault` : ""}
                  {summary.auto_confirmed ? ` · ${summary.auto_confirmed} added automatically` : ""}
                </div>
                {summary.message && <div className="mt-2 text-amber-600 dark:text-amber-400">{summary.message}</div>}
                <Link to={`/review/scan/${scan.id}`} className="btn-primary mt-3 w-full">Review results</Link>
              </div>
            )}
            {phase === "finished" && (job?.status === "failed" || job?.status === "cancelled") && (
              <div className="space-y-2">
                {job?.error && <ErrorBox error={job.error} />}
                <button className="btn-primary w-full" onClick={retry}>Retry processing</button>
              </div>
            )}
          </div>
        )}

        {phase === "duplicate" && scan && (
          <div className="mt-5 rounded-xl bg-sky-500/10 p-3 text-sm ring-1 ring-sky-500/20">
            <div className="font-semibold">You've already imported this exact recording.</div>
            <p className="mt-1 text-slate-600 dark:text-slate-300">Nothing was duplicated. You can rescan it: Pokémon you already confirmed from it are re-linked, not added twice.</p>
            <div className="mt-3 flex gap-2">
              <Link className="btn-ghost flex-1" to={`/review/scan/${scan.id}`}>Open existing scan</Link>
              <button className="btn-primary flex-1" onClick={() => start(true)}>Rescan</button>
            </div>
          </div>
        )}
      </div>

      <div className="mt-6 space-y-3 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
        <p><b className="text-slate-700 dark:text-slate-200">Where your video goes.</b> The recording is uploaded only to your own PokéVault server (your Replit deployment) and processed there with local FFmpeg and OCR. It is never sent to Niantic, to Pokémon GO, or to any AI service.</p>
        <p><b className="text-slate-700 dark:text-slate-200">What's kept.</b> The raw video is deleted after processing unless you choose to keep it. Only the downscaled frames used as evidence are retained, and you can purge them from Scan history.</p>
        <p><b className="text-slate-700 dark:text-slate-200">Big files.</b> Recordings up to {limitMb ? mb(limitMb * 1024 * 1024) : "1 GB"} upload in small pieces. If your connection drops, the upload waits and carries on. If you leave the page, choose the same file again within 24 hours to resume where it stopped.</p>
        <p><b className="text-slate-700 dark:text-slate-200">Recording tips.</b> Scroll the storage grid slowly and pause briefly every screen. Open a Pokémon's detail page for HP and candy, then tap Appraise to read IV bars.</p>
      </div>
    </div>
  );
}
