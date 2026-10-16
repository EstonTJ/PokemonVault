import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Pokemon, Scan } from "../api";
import { DemoTag, Empty, ErrorBox, PageHeader, PokemonRow, Spinner, Stat, StatusPill, useAsync } from "../ui";

type Dash = {
  total: number; unique_species: number; shiny: number; shadow: number; lucky: number; special: Pokemon[];
  special_count: number; unresolved: number; needs_review: number; ready: number; possible_duplicates: number; seen_before: number;
  evolve: { pokemon: Pokemon; reasons: string[] }[]; evolve_count: number;
  power_up: { pokemon: Pokemon; reasons: string[] }[]; power_up_count: number; demo_count: number; scans: Scan[];
  unknown_shiny: number; auto_unchecked: number;
};

export default function Dashboard() {
  const { data, error, loading, reload } = useAsync(() => api<Dash>("/api/dashboard"), []);
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const search = (e: FormEvent) => { e.preventDefault(); nav(`/collection?q=${encodeURIComponent(q)}`); };

  if (loading && !data) return <div className="flex justify-center py-20"><Spinner /></div>;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div>
      <PageHeader title="Your Vault" subtitle="Only Pokémon you've confirmed are counted." />
      {data.demo_count > 0 && (
        <div className="mb-4 flex items-center gap-2 rounded-xl bg-fuchsia-500/10 p-3 text-xs text-fuchsia-700 ring-1 ring-fuchsia-500/20 dark:text-fuchsia-300">
          <DemoTag /> {data.demo_count} demo example records are shown. They are not scan results. Remove them in Settings.
        </div>
      )}
      <form onSubmit={search} className="mb-5">
        <input className="input" placeholder="Search species, nickname or notes…" value={q} onChange={(e) => setQ(e.target.value)} />
      </form>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Confirmed" value={data.total} to={data.auto_unchecked ? "/collection?checked=no" : "/collection"} tone="accent"
          hint={data.auto_unchecked ? `${data.auto_unchecked} auto-added, not yet checked` : undefined} />
        <Stat label="Unique species" value={data.unique_species} />
        <Stat label="Shiny" value={data.shiny} hint={data.unknown_shiny ? `${data.unknown_shiny} not yet checked` : undefined} to="/collection?shiny=yes" />
        <Stat label="Shadow" value={data.shadow} to="/collection?shadow=yes" />
        <Stat label="Rare & special forms" value={data.special_count} to="/collection?special=yes" />
        <Stat label="To review" value={data.unresolved} hint={`${data.needs_review} need attention · ${data.ready} ready`} to="/review" tone={data.unresolved ? "warn" : "default"} />
        <Stat label="Seen before" value={data.seen_before} hint={data.seen_before ? "already in your vault: link them in Review" : undefined} to="/review" />
        <Stat label="Possible duplicates" value={data.possible_duplicates} hint={data.possible_duplicates ? "may be seen twice in one recording" : undefined} to="/review" tone={data.possible_duplicates ? "warn" : "default"} />
        <Stat label="Lucky" value={data.lucky} to="/collection?lucky=yes" />
      </div>

      {data.total === 0 && data.unresolved === 0 && (
        <div className="mt-6">
          <Empty>
            <p className="mb-3 font-medium text-slate-700 dark:text-slate-200">Start by scanning a screen recording of your Pokémon storage.</p>
            <Link to="/upload" className="btn-primary">Upload a recording</Link>
          </Empty>
        </div>
      )}

      <div className="mt-8 grid gap-6 md:grid-cols-2">
        <Section title="Recommended evolutions" count={data.evolve_count} to="/insights?tab=Evolve">
          {data.evolve.length ? data.evolve.map((x) => <PokemonRow key={x.pokemon.id} p={x.pokemon} />) : <Empty>No evolution suggestions yet.</Empty>}
        </Section>
        <Section title="Recommended power-ups" count={data.power_up_count} to="/insights?tab=Power%20Up">
          {data.power_up.length ? data.power_up.map((x) => <PokemonRow key={x.pokemon.id} p={x.pokemon} />) : <Empty>Power-up suggestions need verified IVs.</Empty>}
        </Section>
        <Section title="Rare & special" count={data.special.length}>
          {data.special.length ? data.special.map((p) => <PokemonRow key={p.id} p={p} />) : <Empty>None confirmed yet.</Empty>}
        </Section>
        <Section title="Scan history" to="/scans">
          {data.scans.length ? data.scans.map((s) => (
            <Link key={s.id} to={`/review/scan/${s.id}`} className="card flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 truncate text-sm font-semibold">{s.original_filename} {s.is_demo && <DemoTag />}</div>
                <div className="text-xs text-slate-500">{new Date(s.created_at).toLocaleString()} · {s.summary?.groups ?? 0} items</div>
              </div>
              <StatusPill status={s.status} />
            </Link>
          )) : <Empty>No scans yet.</Empty>}
        </Section>
      </div>
    </div>
  );
}

function Section({ title, count, to, children }: { title: string; count?: number; to?: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          {title} {count !== undefined && <span className="ml-1 text-slate-400">{count}</span>}
        </h2>
        {to && <Link to={to} className="text-xs font-semibold text-vault-500">See all</Link>}
      </div>
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  );
}
