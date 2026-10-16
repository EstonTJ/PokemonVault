import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Candidate, frameUrl, Group, Scan, Species } from "../api";
import { AttrRow, ConfBadge, DemoTag, Empty, ErrorBox, PageHeader, Spinner, StatusPill, TriSelect, useAsync } from "../ui";

export default function Review({ onChange }: { onChange: (n: number) => void }) {
  const { scanId } = useParams();
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const { data, error, loading, reload } = useAsync(async () => {
    if (scanId) return api<Scan>(`/api/scans/${scanId}`);
    return { groups: await api<Group[]>("/api/review") } as Partial<Scan>;
  }, [scanId]);

  const groups = useMemo(() => {
    const g = data?.groups ?? [];
    return filter === "pending" ? g.filter((x) => x.status === "needs_review" || x.status === "ready") : g;
  }, [data, filter]);
  const ready = (data?.groups ?? []).filter((g) => g.status === "ready").length;

  useEffect(() => {
    if (!scanId) onChange((data?.groups ?? []).length);
  }, [data, scanId, onChange]);

  async function confirmReady() {
    if (!scanId) return;
    await api(`/api/scans/${scanId}/confirm-ready`, { method: "POST" });
    reload();
  }

  async function linkMatches() {
    if (!scanId) return;
    await api(`/api/scans/${scanId}/link-matches`, { method: "POST" });
    reload();
  }

  const scan = scanId ? (data as Scan | null) : null;

  return (
    <div>
      <PageHeader
        title={scan ? scan.original_filename : "Review queue"}
        subtitle={scan
          ? <span className="inline-flex items-center gap-2">{scan.is_demo && <DemoTag />}<StatusPill status={scan.status} /> {scan.summary?.groups ?? 0} items · {scan.summary?.keyframes ?? 0} key frames</span>
          : "Items the scanner was unsure about, or that need you to say whether they're the same Pokémon."}
        action={scanId && ready > 0 ? <button className="btn-primary" onClick={confirmReady}>Confirm {ready} ready</button> : undefined}
      />
      {scan && (scan.linkable ?? 0) > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl bg-sky-500/10 p-3 text-sm ring-1 ring-sky-500/30">
          <div className="min-w-0 flex-1">
            <b>{scan.linkable} {scan.linkable === 1 ? "Pokémon is" : "Pokémon are"} already in your vault.</b>{" "}
            <span className="text-slate-600 dark:text-slate-300">
              Each matches exactly one vault Pokémon on species, CP and HP plus exact weight and height or verified IVs.
              Linking records that you saw them again; nothing is duplicated.
            </span>
          </div>
          <button className="btn-primary" onClick={linkMatches}>Link {scan.linkable} matches</button>
        </div>
      )}
      {scan?.summary?.message && <div className="mb-4 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">{scan.summary.message}</div>}
      {scanId && (
        <div className="mb-4 flex gap-2">
          {(["pending", "all"] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)} className={`chip px-3 py-1.5 text-xs ${filter === f ? "bg-vault-500 text-white" : "bg-slate-200/60 dark:bg-white/5"}`}>
              {f === "pending" ? "Pending" : "All items"}
            </button>
          ))}
        </div>
      )}
      {loading && !data && <div className="flex justify-center py-16"><Spinner /></div>}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && groups.length === 0 && (
        <Empty>{scanId ? "Nothing pending for this scan." : <>All caught up. <Link className="text-vault-500" to="/upload">Scan another recording</Link>.</>}</Empty>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {groups.map((g) => <GroupCard key={g.id} g={g} onDone={reload} />)}
      </div>
    </div>
  );
}

function GroupCard({ g, onDone }: { g: Group; onDone: () => void }) {
  const a = g.attributes;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [identity, setIdentity] = useState<string | null>(g.candidates.length ? null : "new");
  const [detail, setDetail] = useState<{ candidates: Candidate[]; appraisal: any } | null>(null);
  const resolved = g.status === "confirmed" || g.status === "rejected";

  useEffect(() => {
    if (g.candidates.length || a.ivs || a.iv_candidates) api(`/api/groups/${g.id}`).then(setDetail).catch(() => {});
  }, [g.id]);

  const [form, setForm] = useState({
    species_id: (a.species_id?.value as string) ?? "",
    species_label: g.species_name ?? "",
    cp: a.cp?.value != null ? String(a.cp.value) : "",
    hp: a.hp?.value != null ? String(a.hp.value) : "",
    nickname: (a.nickname?.value as string) ?? "",
    shiny: (a.shiny?.value as boolean) ?? null,
    shadow: (a.shadow?.value as boolean) ?? null,
    purified: (a.purified?.value as boolean) ?? null,
    lucky: (a.lucky?.value as boolean) ?? null,
    favorite: (a.favorite?.value as boolean) ?? null,
  });

  function edits() {
    const out: Record<string, unknown> = {};
    const orig = (k: string) => a[k]?.value ?? null;
    if (form.species_id && form.species_id !== orig("species_id")) out.species_id = form.species_id;
    if (form.cp !== "" && Number(form.cp) !== orig("cp")) out.cp = Number(form.cp);
    if (form.hp !== "" && Number(form.hp) !== orig("hp")) out.hp = Number(form.hp);
    if ((form.nickname || null) !== orig("nickname")) out.nickname = form.nickname || null;
    for (const k of ["shiny", "shadow", "purified", "lucky", "favorite"] as const) if (form[k] !== orig(k)) out[k] = form[k];
    return out;
  }

  async function confirm() {
    setBusy(true); setErr(null);
    try {
      await api(`/api/groups/${g.id}/confirm`, { method: "POST", body: { identity: g.candidates.length ? identity : "new", edits: editing ? edits() : undefined } });
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function reject() {
    setBusy(true);
    try { await api(`/api/groups/${g.id}/reject`, { method: "POST" }); onDone(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  const title = g.species_name ?? (a.nickname?.value ? `“${a.nickname.value}”` : "Unknown species");
  const needsIdentity = g.candidates.length > 0 && identity === null;
  const needsSpecies = !a.species_id?.value && !(editing && form.species_id);

  return (
    <div className="card overflow-hidden">
      <div className="flex gap-3 p-4">
        <div className="h-28 w-24 shrink-0 overflow-hidden rounded-xl bg-slate-100 dark:bg-white/5">
          {g.best_frame_id ? <img src={frameUrl(g.best_frame_id, g.best_bbox)} alt="Evidence frame" className="h-full w-full object-cover" loading="lazy" />
            : <div className="flex h-full items-center justify-center text-[10px] text-slate-400">no frame</div>}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="truncate text-base font-semibold">{title}</div>
            {g.clear_match && g.status === "needs_review" ? <span className="chip bg-sky-500/15 text-sky-600 dark:text-sky-300" title="Clearly the same as one Pokémon already in your vault">clear match</span>
              : g.link_method === "auto-link" || g.link_method === "link" ? <span className="chip bg-sky-500/15 text-sky-600 dark:text-sky-300" title="Matched exactly one Pokémon already in your vault">{g.link_method === "link" ? "linked" : "auto-linked"}</span>
              : g.link_method === "auto" ? <span className="chip bg-sky-500/15 text-sky-600 dark:text-sky-300">auto-added</span>
              : <StatusPill status={g.status} />}
          </div>
          <div className="text-xs text-slate-500">
            seen {g.observation_count}× · at {(g.first_ts_ms / 1000).toFixed(1)}s · {g.track_key}
          </div>
          <div className="mt-1 divide-y divide-slate-100 dark:divide-white/5">
            <AttrRow label="Species" a={a.species_id} fmt={() => g.species_name ?? String(a.species_id?.value)} />
            <AttrRow label="CP" a={a.cp} />
            {a.hp && <AttrRow label="HP" a={a.hp} />}
            {a.nickname && <AttrRow label="Nickname" a={a.nickname} />}
            {a.ivs && <AttrRow label="IVs" a={a.ivs} fmt={(v) => (v as number[]).join(" / ")} />}
          </div>
        </div>
      </div>

      {g.review_reasons.length > 0 && (
        <ul className="mx-4 mb-3 space-y-1 rounded-xl bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-200">
          {g.review_reasons.map((r, i) => <li key={i}>• {r}</li>)}
        </ul>
      )}

      {detail?.appraisal && (
        <div className="mx-4 mb-3 rounded-xl bg-slate-100 p-3 text-xs dark:bg-white/5">
          <div className="mb-1 flex items-center justify-between font-semibold">Appraisal <span className={`chip ${detail.appraisal.status === "verified" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-amber-500/15 text-amber-600 dark:text-amber-400"}`}>
            {detail.appraisal.status === "verified" ? "IVs verified" : detail.appraisal.status}</span></div>
          {(["atk", "def", "sta"] as const).map((k) => {
            const b = detail.appraisal.bars[k];
            return <div key={k} className="flex justify-between"><span className="uppercase text-slate-500">{k}</span>
              <span>{b?.lo === null || b?.lo === undefined ? "unreadable" : b.lo === b.hi ? b.lo : `${b.lo}–${b.hi}`} <ConfBadge c={b?.confidence} /></span></div>;
          })}
          {detail.appraisal.status !== "verified" && detail.appraisal.iv_candidates?.length > 0 &&
            <div className="mt-1 text-slate-500">{detail.appraisal.iv_candidates.length} IV combinations remain. Exact IVs are withheld until confirmed.</div>}
        </div>
      )}

      {!resolved && g.candidates.length > 0 && (
        <div className="mx-4 mb-3 rounded-xl ring-1 ring-sky-500/30">
          <div className="border-b border-sky-500/20 p-3 text-xs font-semibold text-sky-700 dark:text-sky-300">Is this a Pokémon already in your vault?</div>
          {(detail?.candidates ?? g.candidates).map((c) => (
            <label key={c.pokemon_id} className="flex cursor-pointer items-start gap-2 p-3 text-sm">
              <input type="radio" name={`id-${g.id}`} checked={identity === c.pokemon_id} onChange={() => setIdentity(c.pokemon_id)} className="mt-1" />
              <span>
                <b>Same as</b> {c.pokemon ? `${c.pokemon.nickname || c.pokemon.species_name} · CP ${c.pokemon.cp}` : c.pokemon_id.slice(0, 8)}
                <span className="block text-xs text-slate-500">{c.reasons.join(" ")}</span>
              </span>
            </label>
          ))}
          <label className="flex cursor-pointer items-center gap-2 p-3 text-sm">
            <input type="radio" name={`id-${g.id}`} checked={identity === "new"} onChange={() => setIdentity("new")} />
            <b>A different individual</b> (add as new)
          </label>
        </div>
      )}

      {editing && !resolved && (
        <div className="grid grid-cols-2 gap-3 border-t border-slate-100 p-4 dark:border-white/5">
          <div className="col-span-2"><SpeciesPicker value={form.species_label} onPick={(s) => setForm({ ...form, species_id: s.species_id, species_label: s.name })} /></div>
          <div><span className="label">CP</span><input className="input" inputMode="numeric" value={form.cp} onChange={(e) => setForm({ ...form, cp: e.target.value.replace(/\D/g, "") })} /></div>
          <div><span className="label">HP</span><input className="input" inputMode="numeric" value={form.hp} onChange={(e) => setForm({ ...form, hp: e.target.value.replace(/\D/g, "") })} /></div>
          <div className="col-span-2"><span className="label">Nickname</span><input className="input" maxLength={12} value={form.nickname} onChange={(e) => setForm({ ...form, nickname: e.target.value })} /></div>
          {(["shiny", "shadow", "purified", "lucky", "favorite"] as const).map((k) => (
            <TriSelect key={k} label={k} value={form[k]} onChange={(v) => setForm({ ...form, [k]: v })} />
          ))}
        </div>
      )}

      {err && <div className="mx-4 mb-3"><ErrorBox error={err} /></div>}

      {!resolved && (
        <div className="flex gap-2 border-t border-slate-100 p-3 dark:border-white/5">
          <button className="btn-ghost" onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Correct"}</button>
          <button className="btn-ghost" onClick={reject} disabled={busy} title="Not a Pokémon / bad read">Reject</button>
          <button className="btn-primary ml-auto" onClick={confirm} disabled={busy || needsIdentity || needsSpecies}
            title={needsSpecies ? "Set the species first" : needsIdentity ? "Choose same or different" : undefined}>
            Confirm
          </button>
        </div>
      )}
      {g.status === "confirmed" && g.pokemon_id && (
        <Link to={`/collection/${g.pokemon_id}`} className="block border-t border-slate-100 p-3 text-center text-sm font-semibold text-vault-500 dark:border-white/5">Open in vault</Link>
      )}
    </div>
  );
}

export function SpeciesPicker({ value, onPick }: { value: string; onPick: (s: Species) => void }) {
  const [q, setQ] = useState(value);
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<Species[]>([]);
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => api<Species[]>(`/api/species?q=${encodeURIComponent(q)}&limit=12`).then(setOpts).catch(() => {}), 150);
    return () => window.clearTimeout(t);
  }, [q, open]);
  return (
    <div className="relative">
      <span className="label">Species & form</span>
      <input className="input" value={q} placeholder="Type a name or dex number" onFocus={() => setOpen(true)}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} onBlur={() => window.setTimeout(() => setOpen(false), 150)} />
      {open && opts.length > 0 && (
        <div className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-xl bg-white shadow-soft ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-white/10">
          {opts.map((s) => (
            <button key={s.species_id} type="button" className="flex w-full justify-between px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-white/5"
              onMouseDown={() => { onPick(s); setQ(s.name); setOpen(false); }}>
              <span>{s.name}</span><span className="text-xs text-slate-400">#{s.dex}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
