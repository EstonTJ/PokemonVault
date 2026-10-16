import { useState } from "react";
import { api } from "../api";
import { ErrorBox } from "../ui";

type KeyInfo = { exists: boolean; created_at?: string; last_used_at?: string | null };

export default function Automation({ keyInfo, autoConfirm, onChange }: {
  keyInfo: KeyInfo; autoConfirm: boolean; onChange: () => void;
}) {
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const uploadUrl = `${window.location.origin}/api/scans?filename=recording.mp4`;

  async function createKey() {
    if (keyInfo.exists && !window.confirm("Create a new key? The old one stops working, so you'll need to update your Shortcut.")) return;
    try {
      const r = await api<{ key: string }>("/api/settings/upload-key", { method: "POST" });
      setNewKey(r.key);
      onChange();
    } catch (e: any) { setErr(e.message); }
  }
  async function revoke() {
    if (!window.confirm("Turn off automatic uploads? Your Shortcut will stop working.")) return;
    await api("/api/settings/upload-key", { method: "DELETE" });
    setNewKey(null);
    onChange();
  }
  async function toggleAuto() {
    try { await api("/api/settings", { method: "PUT", body: { auto_confirm_ready: !autoConfirm } }); onChange(); }
    catch (e: any) { setErr(e.message); }
  }
  async function copy(label: string, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(label); window.setTimeout(() => setCopied(null), 1500); }
    catch { window.prompt("Copy this:", text); }
  }

  return (
    <section className="card p-5 text-sm">
      <h2 className="mb-1 font-semibold">Automation</h2>
      <p className="text-slate-500">Upload new screen recordings automatically when you close Pokémon GO.</p>

      <div className="mt-4 rounded-xl bg-slate-50 p-4 dark:bg-white/5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="font-semibold">Upload key</div>
            <div className="text-xs text-slate-500">
              {keyInfo.exists
                ? <>Active since {new Date(keyInfo.created_at!).toLocaleDateString()} · {keyInfo.last_used_at ? `last used ${new Date(keyInfo.last_used_at).toLocaleString()}` : "not used yet"}</>
                : "Off. Create a key to let your Shortcut upload."}
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary px-3 py-2" onClick={createKey}>{keyInfo.exists ? "New key" : "Create key"}</button>
            {keyInfo.exists && <button className="btn-ghost px-3 py-2" onClick={revoke}>Turn off</button>}
          </div>
        </div>
        <p className="mt-2 text-xs text-slate-500">This key can <b>only upload recordings</b>. It can't view, change or delete anything in your vault.</p>

        {newKey && (
          <div className="mt-3 rounded-xl bg-amber-500/10 p-3 ring-1 ring-amber-500/30">
            <div className="text-xs font-semibold text-amber-700 dark:text-amber-300">Copy this key now. It won't be shown again.</div>
            <code className="mt-2 block break-all rounded-lg bg-white p-2 text-xs dark:bg-black/40">{newKey}</code>
            <button className="btn-ghost mt-2 w-full" onClick={() => copy("key", `Bearer ${newKey}`)}>
              {copied === "key" ? "Copied" : "Copy header value (Bearer …)"}
            </button>
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3 rounded-xl bg-slate-50 p-4 dark:bg-white/5">
        <div>
          <div className="font-semibold">Auto-confirm confident results</div>
          <div className="text-xs text-slate-500">
            Adds items the scanner is sure about straight to your vault, marked "auto". A Pokémon you've scanned before is
            linked to its existing entry only when exactly one stored Pokémon matches species, form, CP and nickname (and
            HP when visible). Anything uncertain, such as identical twins or an unclear form, still waits in Review.
          </div>
        </div>
        <button onClick={toggleAuto} role="switch" aria-checked={autoConfirm}
          className={`relative h-7 w-12 shrink-0 rounded-full transition ${autoConfirm ? "bg-vault-500" : "bg-slate-300 dark:bg-white/15"}`}>
          <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all ${autoConfirm ? "left-6" : "left-1"}`} />
        </button>
      </div>

      {err && <div className="mt-3"><ErrorBox error={err} /></div>}

      <details className="mt-4 rounded-xl ring-1 ring-slate-200 dark:ring-white/10" open={!!newKey}>
        <summary className="cursor-pointer p-4 font-semibold">Set up the iPhone Shortcut (about 3 minutes)</summary>
        <div className="space-y-4 px-4 pb-4 text-slate-600 dark:text-slate-300">
          <Step n={1} title="Create the shortcut">
            In the <b>Shortcuts</b> app, tap <b>+</b> and name it <i>Send to PokéVault</i>. Add these actions:
          </Step>
          <Step n={2} title="Find Photos">
            Add <b>Find Photos</b>. Add filters: <b>Media Type</b> is <b>Screen Recording</b> (on some iOS versions:
            <b> Album</b> is <b>Screen Recordings</b>) and <b>Date Taken</b> is in the last <b>30 minutes</b>.
            Sort by <b>Date Taken</b>, <b>Latest First</b>, limit <b>1</b>.
          </Step>
          <Step n={3} title="Only continue if one was found">
            Add <b>If</b> → <i>Photos</i> <b>has any value</b>. Put the next step inside the If.
          </Step>
          <Step n={4} title="Get Contents of URL">
            URL:
            <CopyRow text={uploadUrl} label="url" copied={copied} onCopy={copy} />
            Tap <b>Show More</b>. Method <b>POST</b>. Add a header named <b>Authorization</b> with the value you copied above
            (<code>Bearer pvu_…</code>). Request Body: <b>File</b>, then choose <i>Photos</i>.
          </Step>
          <Step n={5} title="Optional: a notification">
            Add <b>Show Notification</b> with “Sent to PokéVault”.
          </Step>
          <Step n={6} title="Run it whenever you close the game">
            Go to <b>Automation</b> → <b>+</b> → <b>App</b> → choose <b>Pokémon GO</b> → <b>Is Closed</b>.
            Select <b>Run Immediately</b>, then pick <i>Send to PokéVault</i>.
          </Step>
          <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-white/5">
            <b>Your routine:</b> start a screen recording from Control Center, scroll your new catches slowly (open
            detail pages or appraisals if you like), then close Pokémon GO. The upload happens on its own, and closing
            the game twice won't create duplicates. iOS doesn't allow apps to start screen recording, so that one tap
            stays manual. Large recordings upload faster on Wi-Fi.
          </div>
        </div>
      </details>
    </section>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-vault-500 text-xs font-bold text-white">{n}</span>
      <div className="min-w-0 flex-1"><div className="font-semibold text-slate-800 dark:text-slate-100">{title}</div><div className="mt-0.5 text-xs leading-relaxed">{children}</div></div>
    </div>
  );
}

function CopyRow({ text, label, copied, onCopy }: { text: string; label: string; copied: string | null; onCopy: (l: string, t: string) => void }) {
  return (
    <div className="my-1.5 flex items-center gap-2">
      <code className="min-w-0 flex-1 truncate rounded-lg bg-slate-100 px-2 py-1.5 text-[11px] dark:bg-black/40">{text}</code>
      <button className="btn-ghost px-2.5 py-1.5 text-xs" onClick={() => onCopy(label, text)}>{copied === label ? "Copied" : "Copy"}</button>
    </div>
  );
}
