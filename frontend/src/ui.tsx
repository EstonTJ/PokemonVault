import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Attr, Pokemon } from "./api";

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0 max-w-full">
        <h1 className="break-words text-2xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Stat({ label, value, hint, to, tone = "default" }: {
  label: string; value: ReactNode; hint?: string; to?: string; tone?: "default" | "warn" | "accent";
}) {
  const ring = tone === "warn" ? "ring-amber-400/40" : tone === "accent" ? "ring-vault-400/40" : "";
  const body = (
    <div className={`card h-full p-4 ${ring}`}>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 text-3xl font-bold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</div>}
    </div>
  );
  return to ? <Link to={to} className="block transition hover:-translate-y-0.5">{body}</Link> : body;
}

export function ConfBadge({ c }: { c: number | undefined | null }) {
  if (c === undefined || c === null) return null;
  const pct = Math.round(c * 100);
  const tone = c >= 0.85 ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
    : c >= 0.6 ? "bg-amber-500/15 text-amber-600 dark:text-amber-400" : "bg-rose-500/15 text-rose-600 dark:text-rose-400";
  return <span className={`chip ${tone}`} title="Recognition confidence">{pct}%</span>;
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: "bg-slate-500/15 text-slate-600 dark:text-slate-300", processing: "bg-vault-500/15 text-vault-600 dark:text-vault-300",
    running: "bg-vault-500/15 text-vault-600 dark:text-vault-300", review: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    needs_review: "bg-amber-500/15 text-amber-600 dark:text-amber-400", ready: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
    done: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400", confirmed: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
    succeeded: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400", failed: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
    cancelled: "bg-slate-500/15 text-slate-500", rejected: "bg-slate-500/15 text-slate-500",
  };
  return <span className={`chip ${map[status] ?? "bg-slate-500/15"}`}>{status.replace("_", " ")}</span>;
}

export function DemoTag() {
  return <span className="chip bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-300" title="Demo fixture, not a real scan">DEMO</span>;
}

/** Three-state flag display: true, false, unknown (null) shown as a dash, never as "No". */
export function Tri({ v, label }: { v: boolean | null | undefined; label: string }) {
  const text = v === true ? "Yes" : v === false ? "No" : "—";
  return (
    <div className="flex items-center justify-between py-1 text-sm">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className={v === true ? "font-semibold text-vault-500" : v === null || v === undefined ? "text-slate-400" : ""}
        title={v === null || v === undefined ? "Unknown: not detected from video" : undefined}>{text}</span>
    </div>
  );
}

export function TriSelect({ value, onChange, label }: { value: boolean | null; onChange: (v: boolean | null) => void; label: string }) {
  const opts: [string, boolean | null][] = [["Unknown", null], ["Yes", true], ["No", false]];
  return (
    <div>
      <span className="label">{label}</span>
      <div className="flex overflow-hidden rounded-xl ring-1 ring-slate-200 dark:ring-white/10">
        {opts.map(([t, v]) => (
          <button key={t} type="button" onClick={() => onChange(v)}
            className={`flex-1 py-2 text-xs font-semibold ${value === v ? "bg-vault-500 text-white" : "bg-slate-100 dark:bg-white/5"}`}>{t}</button>
        ))}
      </div>
    </div>
  );
}

export function Flags({ p }: { p: Partial<Pokemon> }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {p.shiny && <span className="chip bg-yellow-400/20 text-yellow-600 dark:text-yellow-300">✦ Shiny</span>}
      {p.lucky && <span className="chip bg-amber-400/20 text-amber-600 dark:text-amber-300">Lucky</span>}
      {p.shadow && <span className="chip bg-violet-500/20 text-violet-600 dark:text-violet-300">Shadow</span>}
      {p.purified && <span className="chip bg-sky-400/20 text-sky-600 dark:text-sky-300">Purified</span>}
      {p.favorite && <span className="chip bg-rose-400/20 text-rose-600 dark:text-rose-300">★</span>}
      {p.legendary && <span className="chip bg-indigo-400/20 text-indigo-600 dark:text-indigo-300">Legendary</span>}
      {p.form && <span className="chip bg-teal-400/20 text-teal-600 dark:text-teal-300">{p.form}</span>}
    </span>
  );
}

export function AttrRow({ label, a, fmt }: { label: string; a?: Attr; fmt?: (v: unknown) => string }) {
  const v = a?.value;
  return (
    <div className="flex items-center justify-between gap-2 py-1.5 text-sm">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="flex items-center gap-2">
        {v === undefined || v === null ? <span className="text-slate-400" title="Not visible / not detected">unknown</span>
          : <span className="font-semibold">{fmt ? fmt(v) : String(v)}</span>}
        {a && v !== null && v !== undefined && <ConfBadge c={a.confidence} />}
      </span>
    </div>
  );
}

export function Spinner() {
  return <div className="h-5 w-5 animate-spin rounded-full border-2 border-vault-500 border-t-transparent" />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="card p-8 text-center text-sm text-slate-500 dark:text-slate-400">{children}</div>;
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="rounded-xl bg-rose-500/10 p-3 text-sm text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-300">
      {error}
      {onRetry && <button className="ml-3 font-semibold underline" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-white/10">
      <div className="h-full rounded-full bg-gradient-to-r from-vault-400 to-vault-600 transition-all" style={{ width: `${Math.max(2, value * 100)}%` }} />
    </div>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(e.message || String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

export function PokemonRow({ p }: { p: Pokemon }) {
  return (
    <Link to={`/collection/${p.id}`} className="card flex items-center gap-3 p-3 transition hover:ring-vault-400/50">
      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-vault-500/20 to-fuchsia-500/10 text-xs font-bold text-vault-600 dark:text-vault-300">
        #{p.dex}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-semibold">{p.nickname || p.species_name}</span>
          {p.is_demo && <DemoTag />}
          {!p.user_confirmed && (p.confirmed_via === "auto" || p.confirmed_via === "auto-link") &&
            <span className="chip bg-sky-500/15 text-sky-600 dark:text-sky-300" title="Added by the scanner; not checked by you yet">auto</span>}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
          {p.nickname && <span>{p.species_name} ·</span>}
          <span>CP {p.cp ?? "?"}</span>
          {p.iv_status === "verified" && <span>· {p.iv_atk}/{p.iv_def}/{p.iv_sta}</span>}
          <Flags p={p} />
        </div>
      </div>
      <span className="text-slate-400">›</span>
    </Link>
  );
}
