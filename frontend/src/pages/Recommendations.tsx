import { useSearchParams } from "react-router-dom";
import { api, Pokemon, Score } from "../api";
import { Empty, ErrorBox, PageHeader, PokemonRow, Spinner, useAsync } from "../ui";

type Rec = {
  buckets: Record<string, { pokemon: Pokemon; reasons: string[]; scores: Record<string, Score> }[]>;
  provisional: boolean;
  gamedata: { source: string; timestamp: string; stale: boolean };
};
const TABS = ["Evolve", "Power Up", "Appraise", "Keep", "Review for Transfer"];

export default function Recommendations() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "Evolve";
  const { data, error, loading, reload } = useAsync(() => api<Rec>("/api/recommendations"), []);

  return (
    <div>
      <PageHeader title="Insights" subtitle="Suggestions with reasons. Nothing is ever changed or transferred in the game." />
      {data?.provisional && (
        <div className="mb-4 rounded-xl bg-amber-500/10 p-3 text-xs text-amber-800 ring-1 ring-amber-500/20 dark:text-amber-200">
          Some rankings are provisional. PvE uses PvP move data as a proxy, league scores don't simulate moves,
          {data.gamedata.stale ? " and the game data is out of date." : ""} Game data: {data.gamedata.timestamp}.
        </div>
      )}
      <div className="-mx-4 mb-4 flex gap-2 overflow-x-auto px-4 pb-1 md:mx-0 md:px-0">
        {TABS.map((t) => (
          <button key={t} onClick={() => setParams({ tab: t })}
            className={`chip whitespace-nowrap px-3 py-1.5 text-xs ${tab === t ? "bg-vault-500 text-white" : "bg-slate-200/60 dark:bg-white/5"}`}>
            {t} {data && <span className="opacity-70">{data.buckets[t]?.length ?? 0}</span>}
          </button>
        ))}
      </div>
      {loading && !data && <div className="flex justify-center py-16"><Spinner /></div>}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && (data.buckets[tab]?.length ?? 0) === 0 && <Empty>No Pokémon in this group.</Empty>}
      {tab === "Review for Transfer" && (data?.buckets[tab]?.length ?? 0) > 0 && (
        <p className="mb-3 text-xs text-slate-500">Check shiny status and any sentimental value in the game before transferring anything.</p>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {data?.buckets[tab]?.map((x) => (
          <div key={x.pokemon.id} className="space-y-1.5">
            <PokemonRow p={x.pokemon} />
            <ul className="px-2 text-xs text-slate-600 dark:text-slate-400">{x.reasons.map((r, i) => <li key={i}>• {r}</li>)}</ul>
          </div>
        ))}
      </div>
    </div>
  );
}
