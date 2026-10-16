import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, Scan, uploadVideo } from "../api";
import { ErrorBox, PageHeader, ProgressBar, StatusPill } from "../ui";

type Phase = "idle" | "uploading" | "processing" | "finished" | "duplicate" | "error";

export default function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("auto");
  const [keepVideo, setKeepVideo] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploadFrac, setUploadFrac] = useState(0);
  const [scan, setScan] = useState<Scan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [limitMb, setLimitMb] = useState<number | null>(null);
  const abortRef = useRef<() => void>();

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
    setErr(null); setPhase("uploading"); setUploadFrac(0);
    const { promise, abort } = uploadVideo(file, { kind, keep_video: keepVideo, reprocess }, setUploadFrac);
    abortRef.current = abort;
    try {
      const { data } = await promise;
      setScan(data.scan);
      setPhase(data.duplicate ? "duplicate" : "processing");
    } catch (e) {
      setErr((e as ApiError).message); setPhase("error");
    }
  }

  async function cancel() {
    if (phase === "uploading") { abortRef.current?.(); return; }
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

        {phase === "idle" || phase === "error" ? (
          <button className="btn-primary mt-5 w-full" disabled={!file} onClick={() => start()}>
            {phase === "error" ? "Retry upload" : "Upload & scan"}
          </button>
        ) : null}

        {phase === "uploading" && (
          <div className="mt-5 space-y-2">
            <div className="flex justify-between text-xs text-slate-500"><span>Uploading…</span><span>{Math.round(uploadFrac * 100)}%</span></div>
            <ProgressBar value={uploadFrac} />
            <button className="btn-ghost w-full" onClick={cancel}>Cancel upload</button>
          </div>
        )}

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
        <p><b className="text-slate-700 dark:text-slate-200">Recording tips.</b> Scroll the storage grid slowly and pause briefly every screen. Open a Pokémon's detail page for HP and candy, then tap Appraise to read IV bars.</p>
      </div>
    </div>
  );
}
