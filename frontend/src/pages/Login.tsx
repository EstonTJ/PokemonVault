import { FormEvent, useState } from "react";
import { api } from "../api";
import { ErrorBox } from "../ui";

export default function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/auth/login", { method: "POST", body: { password: pw } });
      onDone();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <div className="mb-6 flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="h-10 w-10" />
          <div>
            <div className="text-xl font-bold">Poké<span className="text-vault-500">Vault</span></div>
            <div className="text-xs text-slate-500">Private collection scanner</div>
          </div>
        </div>
        <label className="label" htmlFor="pw">Owner password</label>
        <input id="pw" type="password" autoComplete="current-password" className="input" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
        {err && <div className="mt-3"><ErrorBox error={err} /></div>}
        <button className="btn-primary mt-4 w-full" disabled={busy || !pw}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="mt-4 text-xs leading-relaxed text-slate-500">
          This is your private deployment. It never asks for Pokémon GO credentials and never connects to the game.
        </p>
      </form>
    </div>
  );
}
