import { useState } from "react";
import { api } from "../api";
import { DemoTag, ErrorBox, PageHeader, Spinner, useAsync } from "../ui";
import Automation from "./Automation";

export default function Settings({ onLogout }: { onLogout: () => void }) {
  const { data, error, loading, reload } = useAsync(() => api("/api/settings"), []);
  const [phrase, setPhrase] = useState("");
  const [vaultPhrase, setVaultPhrase] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [dark, setDark] = useState(document.documentElement.classList.contains("dark"));

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("pv-theme", next ? "dark" : "light"); } catch { /* ignore */ }
  }
  async function run(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try { await fn(); setMsg(ok); reload(); } catch (e: any) { setMsg(e.message); }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageHeader title="Privacy & settings" />

      <section className="card p-5 text-sm leading-relaxed">
        <h2 className="mb-2 font-semibold">How your data is handled</h2>
        <ul className="list-disc space-y-1.5 pl-5 text-slate-600 dark:text-slate-300">
          <li>Recordings are uploaded only to <b>your own PokéVault backend</b> (your Replit deployment). They're processed there with FFmpeg and local OCR.</li>
          <li>PokéVault never asks for Pokémon GO credentials, never connects to the game or any unofficial API, and never automates or modifies the game.</li>
          <li>No analytics, ads, third-party scripts, fonts or AI services. Nothing leaves your server.</li>
          <li>Raw videos are deleted after processing unless you choose to keep one. Only the downscaled frames used as evidence are kept, and you can purge them per scan.</li>
          <li>Media is served only to your signed-in session, never at public URLs.</li>
        </ul>
        {data && (
          <div className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-slate-500 dark:bg-white/5">
            External AI processing: <b>{data.external_ai_opt_in ? "on" : "off (not integrated)"}</b>
          </div>
        )}
      </section>

      {data && <Automation keyInfo={data.upload_key} autoConfirm={data.auto_confirm_ready} onChange={reload} />}

      {loading && !data && <Spinner />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && (
        <section className="card p-5 text-sm">
          <h2 className="mb-2 font-semibold">System</h2>
          <dl className="grid grid-cols-2 gap-y-1.5 text-slate-600 dark:text-slate-300">
            <dt>OCR engine</dt><dd className="text-right">{data.ocr_engine}</dd>
            <dt>Game data</dt><dd className="text-right">{data.gamedata.timestamp}{data.gamedata.stale ? " (stale)" : ""}</dd>
            <dt>Species in reference</dt><dd className="text-right">{data.gamedata.species}</dd>
            <dt>Upload limit</dt><dd className="text-right">{data.limits.max_upload_mb} MB · {Math.round(data.limits.max_duration_s / 60)} min</dd>
            <dt>Uploads per hour</dt><dd className="text-right">{data.limits.max_uploads_per_hour}</dd>
          </dl>
        </section>
      )}

      <section className="card p-5 text-sm">
        <h2 className="mb-1 font-semibold">Appearance</h2>
        <button className="btn-ghost mt-2" onClick={toggleTheme}>{dark ? "Switch to light mode" : "Switch to dark mode"}</button>
      </section>

      <section className="card p-5 text-sm">
        <h2 className="mb-1 flex items-center gap-2 font-semibold">Demo data <DemoTag /></h2>
        <p className="text-slate-500">Example records for trying the interface. Clearly marked DEMO and excluded from exports by default. They're not scan results.</p>
        <div className="mt-3 flex gap-2">
          <button className="btn-ghost" onClick={() => run(() => api("/api/demo/load", { method: "POST" }), "Demo examples loaded.")}>Load examples</button>
          <button className="btn-ghost" onClick={() => run(() => api("/api/demo", { method: "DELETE" }), "Demo examples removed.")}>Remove examples</button>
        </div>
      </section>

      <section className="card p-5 text-sm">
        <h2 className="mb-1 font-semibold">Your data</h2>
        <div className="mt-2 flex flex-wrap gap-2">
          <a className="btn-ghost" href="/api/privacy/export" download>Export all data (JSON)</a>
          <a className="btn-ghost" href="/api/export/collection.csv" download>Collection CSV</a>
        </div>
        <div className="mt-5 rounded-xl p-4 ring-1 ring-amber-500/40">
          <div className="font-semibold text-amber-600 dark:text-amber-400">Clear vault</div>
          <p className="mt-1 text-xs text-slate-500">
            Removes every Pokémon from your vault. Your scans stay, and their items go back to Review so you can
            confirm them again. Nothing changes in Pokémon GO. Type <b>DELETE</b> to confirm.
          </p>
          <div className="mt-2 flex gap-2">
            <input className="input" value={vaultPhrase} onChange={(e) => setVaultPhrase(e.target.value)} placeholder="DELETE"
              autoCapitalize="characters" autoComplete="off" />
            <button className="btn-danger whitespace-nowrap" disabled={vaultPhrase !== "DELETE"}
              onClick={() => run(async () => {
                const r = await api<{ removed: number }>("/api/pokemon/clear", { method: "POST", body: { confirm: vaultPhrase } });
                setVaultPhrase("");
                return r;
              }, "Vault cleared. Your scans' items are back in Review.")}>Clear vault</button>
          </div>
        </div>
        <div className="mt-4 rounded-xl p-4 ring-1 ring-rose-500/30">
          <div className="font-semibold text-rose-600">Delete everything</div>
          <p className="mt-1 text-xs text-slate-500">Permanently deletes every Pokémon, scan, frame, video and setting on this server. Type <b>DELETE ALL</b> to confirm.</p>
          <div className="mt-2 flex gap-2">
            <input className="input" value={phrase} onChange={(e) => setPhrase(e.target.value)} placeholder="DELETE ALL" />
            <button className="btn-danger" disabled={phrase !== "DELETE ALL"}
              onClick={() => run(() => api("/api/privacy/delete-all", { method: "POST", body: { confirm: phrase } }), "All data deleted.")}>Delete</button>
          </div>
        </div>
      </section>

      {msg && <div className="rounded-xl bg-slate-100 p-3 text-sm dark:bg-white/5">{msg}</div>}

      <button className="btn-ghost w-full" onClick={() => api("/api/auth/logout", { method: "POST" }).finally(onLogout)}>Sign out</button>
    </div>
  );
}
