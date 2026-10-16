import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, Pokemon } from "../api";
import { Empty, ErrorBox, PageHeader, PokemonRow, Spinner, useAsync } from "../ui";

const FLAGS = ["shiny", "shadow", "purified", "lucky", "favorite"] as const;

export default function Collection() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [showFilters, setShowFilters] = useState(false);
  const query = useMemo(() => {
    const p = new URLSearchParams(params);
    if (!p.get("sort")) p.set("sort", "recent");
    return p.toString();
  }, [params]);
  const { data, error, loading, reload } = useAsync(() => api<Pokemon[]>(`/api/pokemon?${query}`), [query]);

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
      <div className="grid gap-2 md:grid-cols-2">
        {data?.map((p) => <PokemonRow key={p.id} p={p} />)}
      </div>
    </div>
  );
}
