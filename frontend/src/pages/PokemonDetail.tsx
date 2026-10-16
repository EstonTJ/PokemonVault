import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, frameUrl, Pokemon, Recommendation } from "../api";
import { ConfBadge, DemoTag, ErrorBox, Flags, PageHeader, Spinner, Tri, TriSelect, useAsync } from "../ui";
import { SpeciesPicker } from "./Review";

type Detail = Pokemon & {
  recommendation: Recommendation;
  evidence: { id: number; kind: string; scan_id: number | null; frame_id: number | null; bbox: number[] | null; created_at: string }[];
  corrections: { field: string; old: unknown; new: unknown; at: string }[];
};

const SCORE_LABELS: Record<string, string> = {
  rarity: "Collection rarity", pve: "PvE usefulness", great: "Great League", ultra: "Ultra League",
  master: "Master League", evolution: "Evolution priority", stardust: "Stardust investment",
};
const ACTION_TONE: Record<string, string> = {
  Keep: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300", Evolve: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  "Power Up": "bg-vault-500/15 text-vault-700 dark:text-vault-300", Appraise: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "Review for Transfer": "bg-slate-500/15 text-slate-700 dark:text-slate-300",
};

export default function PokemonDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: p, error, loading, reload } = useAsync(() => api<Detail>(`/api/pokemon/${id}`), [id]);
  const [editing, setEditing] = useState(false);

  if (loading && !p) return <div className="flex justify-center py-20"><Spinner /></div>;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!p) return null;
  const prov = p.provenance ?? {};
  const rec = p.recommendation;

  async function remove() {
    if (!window.confirm("Remove this Pokémon from your vault? (Nothing changes in the game.)")) return;
    await api(`/api/pokemon/${p!.id}`, { method: "DELETE" });
    nav("/collection");
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title={p.nickname || p.species_name}
        subtitle={<span className="inline-flex flex-wrap items-center gap-2">#{p.dex} {p.nickname && p.species_name} · {p.types.join(" / ")} {p.is_demo && <DemoTag />} <Flags p={p} /></span>}
        action={<button className="btn-ghost" onClick={() => setEditing(!editing)}>{editing ? "Close" : "Edit"}</button>}
      />

      {editing ? <EditForm p={p} onSaved={() => { setEditing(false); reload(); }} /> : (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card p-4">
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-500">Stats</h3>
            <Field label="CP" v={p.cp} prov={prov.cp} />
            <Field label="HP" v={p.hp} prov={prov.hp} />
            <Field label="IVs" v={p.iv_status === "verified" ? `${p.iv_atk} / ${p.iv_def} / ${p.iv_sta}` : p.iv_status === "range" ? `${p.iv_candidates?.length ?? "?"} possible combos` : null}
              prov={prov.ivs} note={p.iv_status !== "verified" ? "Not verified: appraise to confirm" : undefined} />
            <Field label="Form" v={p.form ?? "Normal"} prov={prov.form} />
            <div className="mt-2 border-t border-slate-100 pt-2 dark:border-white/5">
              <Tri label="Shiny" v={p.shiny} /><Tri label="Shadow" v={p.shadow} /><Tri label="Purified" v={p.purified} />
              <Tri label="Lucky" v={p.lucky} /><Tri label="Favorite" v={p.favorite} />
            </div>
            <div className="mt-2 text-xs text-slate-500">
              Confidence <ConfBadge c={p.confidence} /> · {p.user_confirmed ? `confirmed (${p.confirmed_via})` : p.confirmed_via === "auto" || p.confirmed_via === "auto-link" ? "added automatically, not checked by you" : "not user-confirmed"}
            </div>
            {!p.user_confirmed && (p.confirmed_via === "auto" || p.confirmed_via === "auto-link") && (
              <button className="btn-ghost mt-2 w-full" onClick={() => api(`/api/pokemon/${p.id}/mark-checked`, { method: "POST" }).then(reload)}>
                Looks right: mark as checked
              </button>
            )}
          </div>

          <div className="card p-4">
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-500">Suggested actions</h3>
            {rec.provisional && <div className="mb-2 text-xs text-amber-600 dark:text-amber-400">Provisional: see the notes on each score.</div>}
            <div className="space-y-2">
              {rec.actions.map((a) => (
                <div key={a.action} className="rounded-xl bg-slate-50 p-3 dark:bg-white/5">
                  <span className={`chip ${ACTION_TONE[a.action]}`}>{a.action}</span>
                  <ul className="mt-1.5 space-y-0.5 text-xs text-slate-600 dark:text-slate-300">{a.reasons.map((r, i) => <li key={i}>• {r}</li>)}</ul>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-slate-500">PokéVault never transfers or changes Pokémon. Suggestions only.</p>
          </div>

          <div className="card p-4 md:col-span-2">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-500">Scores</h3>
            <div className="grid gap-3 md:grid-cols-2">
              {Object.entries(rec.scores).map(([k, s]) => (
                <details key={k} className="rounded-xl bg-slate-50 p-3 dark:bg-white/5">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-sm">
                    <span>{SCORE_LABELS[k] ?? k}{s.provisional && <span className="ml-1 text-[10px] text-amber-500">provisional</span>}</span>
                    <span className="font-semibold tabular-nums">{Math.round(s.value * 100)}</span>
                  </summary>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10"><div className="h-full bg-vault-500" style={{ width: `${s.value * 100}%` }} /></div>
                  <ul className="mt-2 space-y-0.5 text-xs text-slate-600 dark:text-slate-300">{s.reasons.map((r, i) => <li key={i}>• {r}</li>)}</ul>
                </details>
              ))}
            </div>
            {rec.gamedata && <p className="mt-3 text-[11px] text-slate-500">Game data: {rec.gamedata.source}, {rec.gamedata.timestamp}{rec.gamedata.stale ? " (stale)" : ""}</p>}
          </div>

          {p.evidence.length > 0 && (
            <div className="card p-4 md:col-span-2">
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-500">Evidence</h3>
              <div className="flex gap-3 overflow-x-auto pb-1">
                {p.evidence.map((e) => (
                  <div key={e.id} className="w-28 shrink-0 text-center text-[10px] text-slate-500">
                    {e.frame_id ? <img src={frameUrl(e.frame_id, e.bbox)} alt="evidence" className="h-32 w-28 rounded-lg object-cover" loading="lazy"
                      onError={(ev) => ((ev.target as HTMLImageElement).style.display = "none")} /> : <div className="flex h-32 items-center justify-center rounded-lg bg-slate-100 dark:bg-white/5">frame purged</div>}
                    scan #{e.scan_id ?? "—"} · {e.kind === "auto-link" ? "seen again (auto-linked)" : e.kind === "reimport" ? "re-import" : "first seen"}
                  </div>
                ))}
              </div>
            </div>
          )}

          {p.notes && <div className="card p-4 text-sm md:col-span-2"><h3 className="mb-1 text-sm font-semibold uppercase tracking-wider text-slate-500">Notes</h3>{p.notes}</div>}

          {p.corrections.length > 0 && (
            <div className="card p-4 text-xs md:col-span-2">
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-500">Edit history</h3>
              {p.corrections.map((c, i) => <div key={i} className="py-0.5">{new Date(c.at).toLocaleString()}: <b>{c.field}</b> {JSON.stringify(c.old)} → {JSON.stringify(c.new)}</div>)}
            </div>
          )}
          <button className="btn-ghost text-rose-600 md:col-span-2" onClick={remove}>Remove from vault</button>
        </div>
      )}
    </div>
  );
}

function Field({ label, v, prov, note }: { label: string; v: unknown; prov?: any; note?: string }) {
  return (
    <div className="flex items-center justify-between gap-2 py-1.5 text-sm">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="flex items-center gap-2 text-right">
        {v === null || v === undefined ? <span className="text-slate-400">unknown</span> : <span className="font-semibold">{String(v)}</span>}
        {prov?.confidence !== undefined && <ConfBadge c={prov.confidence} />}
        {prov?.method === "user" && <span className="chip bg-slate-500/15">edited</span>}
        {note && <span className="text-[10px] text-amber-500">{note}</span>}
      </span>
    </div>
  );
}

function EditForm({ p, onSaved }: { p: Detail; onSaved: () => void }) {
  const [f, setF] = useState({
    species_id: p.species_id, species_label: p.species_name, cp: p.cp ?? "", hp: p.hp ?? "", nickname: p.nickname ?? "",
    iv_atk: p.iv_atk ?? "", iv_def: p.iv_def ?? "", iv_sta: p.iv_sta ?? "", notes: p.notes ?? "",
    shiny: p.shiny, shadow: p.shadow, purified: p.purified, lucky: p.lucky, favorite: p.favorite,
  });
  const [err, setErr] = useState<string | null>(null);
  const num = (v: unknown) => (v === "" || v === null ? null : Number(v));

  async function save() {
    const body: Record<string, unknown> = {
      species_id: f.species_id, cp: num(f.cp), hp: num(f.hp), nickname: f.nickname || null, notes: f.notes,
      iv_atk: num(f.iv_atk), iv_def: num(f.iv_def), iv_sta: num(f.iv_sta),
      shiny: f.shiny, shadow: f.shadow, purified: f.purified, lucky: f.lucky, favorite: f.favorite,
    };
    for (const k of Object.keys(body)) if ((p as any)[k] === body[k]) delete body[k];
    try { await api(`/api/pokemon/${p.id}`, { method: "PATCH", body }); onSaved(); } catch (e: any) { setErr(e.message); }
  }
  const digits = (k: keyof typeof f, max = 4) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value.replace(/\D/g, "").slice(0, max) });

  return (
    <div className="card grid grid-cols-2 gap-3 p-4 md:grid-cols-3">
      <div className="col-span-2 md:col-span-3"><SpeciesPicker value={f.species_label} onPick={(s) => setF({ ...f, species_id: s.species_id, species_label: s.name })} /></div>
      <div><span className="label">CP</span><input className="input" inputMode="numeric" value={f.cp} onChange={digits("cp")} /></div>
      <div><span className="label">HP</span><input className="input" inputMode="numeric" value={f.hp} onChange={digits("hp")} /></div>
      <div><span className="label">Nickname</span><input className="input" maxLength={12} value={f.nickname} onChange={(e) => setF({ ...f, nickname: e.target.value })} /></div>
      <div><span className="label">Attack IV</span><input className="input" inputMode="numeric" value={f.iv_atk} onChange={digits("iv_atk", 2)} /></div>
      <div><span className="label">Defense IV</span><input className="input" inputMode="numeric" value={f.iv_def} onChange={digits("iv_def", 2)} /></div>
      <div><span className="label">HP IV</span><input className="input" inputMode="numeric" value={f.iv_sta} onChange={digits("iv_sta", 2)} /></div>
      {(["shiny", "shadow", "purified", "lucky", "favorite"] as const).map((k) => (
        <TriSelect key={k} label={k} value={f[k]} onChange={(v) => setF({ ...f, [k]: v })} />
      ))}
      <div className="col-span-2 md:col-span-3"><span className="label">Notes</span><textarea className="input min-h-[80px]" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
      {err && <div className="col-span-2 md:col-span-3"><ErrorBox error={err} /></div>}
      <button className="btn-primary col-span-2 md:col-span-3" onClick={save}>Save changes</button>
    </div>
  );
}
