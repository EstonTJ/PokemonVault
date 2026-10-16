export type Attr = {
  value: unknown;
  confidence: number;
  method?: string;
  frame_id?: number | null;
  support?: number;
  alternatives?: unknown[];
};

export type Job = {
  id: number; status: string; progress: number; stage: string; attempts: number; max_attempts: number;
  error: string | null; cancel_requested: boolean;
};

export type Scan = {
  id: number; created_at: string; original_filename: string; size_bytes: number; kind: string; status: string;
  keep_video: boolean; video_stored: boolean; duration_s: number | null; frames_retained: number; is_demo: boolean;
  summary: Record<string, any>; error: string | null; job: Job | null; groups?: Group[]; linkable?: number;
};

export type Candidate = { pokemon_id: string; score: number; reasons: string[]; pokemon?: Pokemon };

export type Group = {
  id: number; scan_id: number; track_key: string; status: string; attributes: Record<string, Attr>;
  species_name: string | null; review_reasons: string[]; candidates: Candidate[]; pokemon_id: string | null;
  observation_count: number; first_ts_ms: number; best_frame_id: number | null; best_bbox: number[] | null; link_method: string | null; clear_match?: boolean;
};

export type Pokemon = {
  id: string; species_id: string; species_name: string; dex: number; types: string[]; form: string | null;
  nickname: string | null; cp: number | null; hp: number | null; iv_atk: number | null; iv_def: number | null;
  iv_sta: number | null; iv_status: string; iv_candidates: number[][] | null; shiny: boolean | null;
  shadow: boolean | null; purified: boolean | null; lucky: boolean | null; favorite: boolean | null;
  confidence: number; provenance: Record<string, any>; user_confirmed: boolean; confirmed_via: string; notes: string;
  is_demo: boolean; legendary: boolean; special_form: boolean; created_at: string; last_seen_at: string;
};

export type Score = { value: number; reasons: string[]; provisional: boolean };
export type Recommendation = {
  scores: Record<string, Score>; actions: { action: string; reasons: string[] }[]; provisional: boolean;
  gamedata?: { source: string; timestamp: string; stale: boolean };
};

export type Species = { species_id: string; name: string; dex: number; form: string | null; types: string[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { "X-PokeVault": "1" };
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, { method, headers, body, credentials: "same-origin" });
  if (res.status === 401 && !path.startsWith("/api/auth")) onUnauthorized();
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const frameUrl = (frameId: number, bbox?: number[] | null) =>
  `/api/frames/${frameId}/image${bbox ? `?crop=${bbox.map((v) => Math.round(v)).join(",")}` : ""}`;

// ------------------------------------------------------------------ resumable chunked uploads

export type UploadProgress = {
  sentBytes: number; totalBytes: number; resumedBytes: number; bytesPerSec: number; phase: "sending" | "waiting" | "finishing";
};

type UploadSession = { id: string; size: number; chunk_size: number; total_chunks: number; received: number[]; resumed: boolean };

const PARALLEL = 3;
const MAX_TRIES = 8;

async function sha256Hex(buf: ArrayBuffer): Promise<string | null> {
  try {
    if (!crypto?.subtle) return null; // non-secure contexts have no SubtleCrypto; the server still checks length
    const d = await crypto.subtle.digest("SHA-256", buf);
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((res, rej) => {
  const t = window.setTimeout(res, ms);
  signal.addEventListener("abort", () => { window.clearTimeout(t); rej(new ApiError(0, "Upload paused.")); }, { once: true });
});

async function waitForOnline(signal: AbortSignal) {
  if (navigator.onLine !== false) return;
  await new Promise<void>((res, rej) => {
    const on = () => { window.removeEventListener("online", on); res(); };
    window.addEventListener("online", on);
    signal.addEventListener("abort", () => { window.removeEventListener("online", on); rej(new ApiError(0, "Upload paused.")); }, { once: true });
  });
}

/**
 * Upload `file` in 8 MB chunks with per-chunk checksums, retries and resume.
 * Pausing (abort) keeps the server-side session; picking the same file again resumes it.
 */
export async function uploadResumable(
  file: File,
  params: { kind: string; keep_video: boolean; reprocess?: boolean },
  onProgress: (p: UploadProgress) => void,
  signal: AbortSignal,
  onSession?: (id: string) => void,
): Promise<{ status: number; data: any }> {
  const session = await api<UploadSession>("/api/uploads", {
    method: "POST",
    body: { filename: file.name, size: file.size, last_modified: file.lastModified, ...params, reprocess: !!params.reprocess },
  });
  onSession?.(session.id);
  const done = new Set(session.received);
  const chunkBytes = (i: number) => Math.min(session.chunk_size, file.size - i * session.chunk_size);
  const resumedBytes = [...done].reduce((n, i) => n + chunkBytes(i), 0);
  let sent = resumedBytes;
  const started = performance.now();
  let phase: UploadProgress["phase"] = "sending";
  const report = () => {
    const secs = Math.max(0.5, (performance.now() - started) / 1000);
    onProgress({ sentBytes: sent, totalBytes: file.size, resumedBytes, bytesPerSec: (sent - resumedBytes) / secs, phase });
  };
  report();

  const todo = Array.from({ length: session.total_chunks }, (_, i) => i).filter((i) => !done.has(i));

  async function sendChunk(i: number) {
    const blob = file.slice(i * session.chunk_size, i * session.chunk_size + chunkBytes(i));
    const buf = await blob.arrayBuffer();
    const sum = await sha256Hex(buf);
    for (let attempt = 1; ; attempt++) {
      if (signal.aborted) throw new ApiError(0, "Upload paused.");
      await waitForOnline(signal);
      try {
        const res = await fetch(`/api/uploads/${session.id}/chunks/${i}`, {
          method: "PUT", body: buf, signal, credentials: "same-origin",
          headers: { "X-PokeVault": "1", "Content-Type": "application/octet-stream", ...(sum ? { "X-Chunk-SHA256": sum } : {}) },
        });
        if (res.ok) { sent += buf.byteLength; report(); return; }
        if (res.status === 401 || res.status === 404 || res.status === 409 || res.status === 413) {
          let msg = res.statusText;
          try { msg = (await res.json()).detail ?? msg; } catch { /* ignore */ }
          throw new ApiError(res.status, typeof msg === "string" ? msg : "Upload failed.");
        }
        // 5xx, 422 (corrupted in transit), 429 etc: retry
      } catch (e) {
        if (signal.aborted) throw new ApiError(0, "Upload paused.");
        if (e instanceof ApiError && e.status !== 0) throw e;
      }
      if (attempt >= MAX_TRIES) throw new ApiError(0, `Chunk ${i + 1} kept failing. Check your connection, then tap Upload to resume.`);
      phase = "waiting"; report();
      await sleep(Math.min(15000, 1000 * 2 ** (attempt - 1)), signal);
      phase = "sending"; report();
    }
  }

  let next = 0;
  const workers = Array.from({ length: Math.min(PARALLEL, todo.length) }, async () => {
    while (next < todo.length) {
      const i = todo[next++];
      await sendChunk(i);
    }
  });
  await Promise.all(workers);

  phase = "finishing"; report();
  const res = await fetch(`/api/uploads/${session.id}/complete`, {
    method: "POST", credentials: "same-origin", headers: { "X-PokeVault": "1" }, signal,
  });
  let data: any = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok) {
    const d = data?.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : d?.message ?? `Upload failed (${res.status})`);
  }
  return { status: res.status, data };
}

export const cancelUpload = (id: string) => api(`/api/uploads/${id}`, { method: "DELETE" }).catch(() => {});
