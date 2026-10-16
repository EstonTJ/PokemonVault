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
  summary: Record<string, any>; error: string | null; job: Job | null; groups?: Group[];
};

export type Candidate = { pokemon_id: string; score: number; reasons: string[]; pokemon?: Pokemon };

export type Group = {
  id: number; scan_id: number; track_key: string; status: string; attributes: Record<string, Attr>;
  species_name: string | null; review_reasons: string[]; candidates: Candidate[]; pokemon_id: string | null;
  observation_count: number; first_ts_ms: number; best_frame_id: number | null; best_bbox: number[] | null; link_method: string | null;
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

/** Upload a recording as the raw request body so the server can enforce limits while streaming. */
export function uploadVideo(
  file: File,
  params: { kind: string; keep_video: boolean; reprocess?: boolean },
  onProgress: (frac: number) => void,
): { promise: Promise<{ status: number; data: any }>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const q = new URLSearchParams({
    filename: file.name, kind: params.kind, keep_video: String(params.keep_video), reprocess: String(!!params.reprocess),
  });
  const promise = new Promise<{ status: number; data: any }>((resolve, reject) => {
    xhr.open("POST", `/api/scans?${q.toString()}`);
    xhr.setRequestHeader("X-PokeVault", "1");
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data: any = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve({ status: xhr.status, data });
      else reject(new ApiError(xhr.status, data?.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload. Check your connection and retry."));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

export const frameUrl = (frameId: number, bbox?: number[] | null) =>
  `/api/frames/${frameId}/image${bbox ? `?crop=${bbox.map((v) => Math.round(v)).join(",")}` : ""}`;
