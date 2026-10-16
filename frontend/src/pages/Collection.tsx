import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, Pokemon } from "../api";
import { Empty, ErrorBox, PageHeader, PokemonRow, Spinner, useAsync } from "../ui";

const FLAGS = ["shiny", "shadow", "purified", "lucky", "favorite"] as const;

export default function Collection() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [showFilters, setShowFilters] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const exitSelect = () => { setSelecting(false); setSelected(new Set()); setConfirming(false); };

  async function removeSelected() {
    setBusy(true);
    try {
      const r = await api<{ removed: number }>("/api/pokemon/bulk-delete", { method: "POST", body: { ids: [...selected] } });
      setNotice(`Removed ${r.removed} from your vault. Their scan items are back in Review.`);
      exitSelect();
      reload();
    } catch (e: any) {
      setNotice(e.message);
    } finally {
      setBusy(false);
    }
  }
  const query = useMemo(() => {
    const p = new URLSearchParams(params);
    if (!p.get("sort")) p.set("sort", "recent");
    return p.toString();
  }, [params]);
  const { data, error, loading, reload } = useAsync(() => api<Pokemon[]>(`/api/pokemon?${query}`), [query]);
  // Never act on Pokémon that are no longer shown: a new search/filter starts a fresh selection.
  useEffect(() => { setSelected(new Set()); setConfirming(false); }, [query]);

  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    setParams(p, { replace: true });
  };
  const activeFilters = FLAGS.filter((f) => params.get(f)).length + (params.get("iv_status") ? 1 : 0) + (params.get("special") ? 1 : 0);

  return (
    <div>
      <PageHeader title="Vault" subtitle={data ? `${data.length} Pokémon` : undefined}
        action={
          <div className="flex gap-2">
            {data && data.length > 0 && (
              <button className={`btn-ghost px-3 ${selecting ? "ring-2 ring-vault-500" : ""}`} onClick={() => (selecting ? exitSelect() : setSelecting(true))}>
                {selecting ? "Done" : "Select"}
              </button>
            )}
            <a className="btn-ghost px-3" href="/api/export/collection.csv" download>CSV</a>
            <a className="btn-ghost px-3" href="/api/export/collection.json" download>JSON</a>
          </div>
        } />
      <div className="mb-3 flex gap-2">
        <form className="flex-1" onSubmit={(e) => { e.preventDefault(); set("q", q || null); }}>
          <input className="input" placeholder="Search species, nickname, notes" value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => set("q", q || null)} />
        </form>
        <button className={`btn-ghost px-3 ${activeFilters ? "ring-2 ring-vault-500" : ""}`} onClick={() => setShowFilters(!showFilters)}>
          Filters{activeFilters ? ` · ${activeFilters}` : ""}
        </button>
      </div>
      {showFilters && (
        <div className="card mb-4 grid grid-cols-2 gap-3 p-4 md:grid-cols-4">
          {FLAGS.map((f) => (
            <div key={f}>
              <span className="label">{f}</span>
              <select className="input" value={params.get(f) ?? ""} onChange={(e) => set(f, e.target.value || null)}>
                <option value="">Any</option><option value="yes">Yes</option><option value="no">No</option><option value="unknown">Unknown</option>
              </select>
            </div>
          ))}
          <div>
            <span className="label">IVs</span>
            <select className="input" value={params.get("iv_status") ?? ""} onChange={(e) => set("iv_status", e.target.value || null)}>
              <option value="">Any</option><option value="verified">Verified</option><option value="range">Range only</option><option value="unknown">Unknown</option>
            </select>
          </div>
          <div>
            <span className="label">Rare / special</span>
            <select className="input" value={params.get("special") ?? ""} onChange={(e) => set("special", e.target.value || null)}>
              <option value="">Any</option><option value="yes">Only rare & forms</option>
            </select>
          </div>
          <div>
            <span className="label">Sort</span>
            <select className="input" value={params.get("sort") ?? "recent"} onChange={(e) => set("sort", e.target.value)}>
              <option value="recent">Recently added</option><option value="cp">CP</option><option value="dex">Dex number</option>
              <option value="name">Name</option><option value="confidence">Confidence</option>
            </select>
          </div>
          <div>
            <span className="label">Order</span>
            <select className="input" value={params.get("desc") ?? "true"} onChange={(e) => set("desc", e.target.value)}>
              <option value="true">Descending</option><option value="false">Ascending</option>
            </select>
          </div>
          <button className="btn-ghost col-span-2 md:col-span-4" onClick={() => { setParams(new URLSearchParams()); setQ(""); }}>Clear filters</button>
        </div>
      )}
      {loading && !data && <div className="flex justify-center py-16"><Spinner /></div>}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && data.length === 0 && <Empty>No Pokémon match. Confirmed scan results appear here.</Empty>}
      {notice && <div className="mb-3 rounded-xl bg-slate-100 p-3 text-sm dark:bg-white/5">{notice}</div>}
      <div className={`grid gap-2 md:grid-cols-2 ${selecting ? "pb-28" : ""}`}>
        {data?.map((p) => selecting ? (
          <label key={p.id} className={`relative block cursor-pointer rounded-2xl ${selected.has(p.id) ? "ring-2 ring-vault-500" : ""}`}>
            <input type="checkbox" className="absolute left-3 top-1/2 z-10 h-5 w-5 -translate-y-1/2 accent-vault-500"
              checked={selected.has(p.id)} onChange={() => toggle(p.id)} aria-label={`Select ${p.nickname || p.species_name}`} />
            <div className="pointer-events-none pl-9"><PokemonRow p={p} /></div>
          </label>
        ) : <PokemonRow key={p.id} p={p} />)}
      </div>

      {selecting && data && (
        <div className="fixed inset-x-0 bottom-16 z-30 px-4 md:bottom-4 md:left-56">
          <div className="card mx-auto max-w-3xl p-3">
            {!confirming ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{selected.size} selected</span>
                <button className="btn-ghost px-3 py-1.5 text-xs" onClick={() => setSelected(new Set(data.map((p) => p.id)))}>
                  Select all shown ({data.length})
                </button>
                {selected.size > 0 && <button className="btn-ghost px-3 py-1.5 text-xs" onClick={() => setSelected(new Set())}>Clear selection</button>}
                <button className="btn-danger ml-auto px-3 py-1.5" disabled={selected.size === 0} onClick={() => setConfirming(true)}>
                  Remove {selected.size || ""}
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm">
                  Remove <b>{selected.size}</b> Pokémon from your vault? Their scan items go back to Review. Nothing changes in the game.
                </span>
                <div className="ml-auto flex gap-2">
                  <button className="btn-ghost px-3 py-1.5" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
                  <button className="btn-danger px-3 py-1.5" onClick={removeSelected} disabled={busy}>
                    {busy ? "Removing…" : `Remove ${selected.size}`}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
