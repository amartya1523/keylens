import { useEffect, useState } from "react";
import { api, del, patch, post } from "../api";
import { Card, CodeBlock, Empty } from "../components/ui";
import { SOURCE_LABEL, ago, compact, num, usd } from "../format";

const KINDS = ["backend", "cli", "browser", "notebook", "agent", "other"];

export default function Apps() {
  const [apps, setApps] = useState<any[]>([]);
  const [keys, setKeys] = useState<any[]>([]);
  const [form, setForm] = useState({ key_id: "", name: "", kind: "backend", budget_usd: "" });
  const [created, setCreated] = useState<any>(null);
  const [error, setError] = useState("");

  const load = () => { api("/api/apps").then(setApps); api("/api/keys").then(setKeys); };
  useEffect(load, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      const res = await post("/api/apps", {
        key_id: Number(form.key_id || keys[0]?.id), name: form.name, kind: form.kind,
        budget_usd: form.budget_usd ? Number(form.budget_usd) : null,
      });
      setCreated({ ...res, name: form.name });
      setForm({ ...form, name: "", budget_usd: "" });
      load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const rotate = async (a: any) => {
    if (!confirm(`Rotate the virtual key for ${a.name}? The old one stops working immediately.`)) return;
    const res = await post(`/api/apps/${a.id}/rotate`);
    setCreated({ ...res, name: a.name });
    load();
  };

  return (
    <>
      <h1>Apps & virtual keys</h1>
      <div className="sub">
        Give every app its own virtual key. The app points its base URL at KeyLens; KeyLens swaps in the real key.
        That is how KeyLens knows <i>which</i> app used the key, and lets you pause one app without rotating the real key.
      </div>

      {!keys.length ? (
        <Card><Empty icon="⚿" title="Add a provider key first">Apps are attached to one of your keys.</Empty></Card>
      ) : (
        <Card title="New app">
          <form onSubmit={create} className="grid" style={{ gridTemplateColumns: "1.4fr 1.4fr 1fr 1fr auto", alignItems: "end" }}>
            <label className="field" style={{ margin: 0 }}>Provider key
              <select value={form.key_id} onChange={(e) => setForm({ ...form, key_id: e.target.value })}>
                {keys.map((k) => <option key={k.id} value={k.id}>{k.label} · {k.masked}</option>)}
              </select>
            </label>
            <label className="field" style={{ margin: 0 }}>App name
              <input placeholder="e.g. Support bot backend" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </label>
            <label className="field" style={{ margin: 0 }}>Kind
              <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                {KINDS.map((k) => <option key={k}>{k}</option>)}
              </select>
            </label>
            <label className="field" style={{ margin: 0 }}>Monthly budget $ (list price)
              <input type="number" step="0.01" min="0" placeholder="none" value={form.budget_usd} onChange={(e) => setForm({ ...form, budget_usd: e.target.value })} />
            </label>
            <button className="btn primary">Create</button>
          </form>
          {error && <div className="error">{error}</div>}
        </Card>
      )}

      {created && (
        <div className="card mt" style={{ borderColor: "var(--acc)" }}>
          <div className="row between">
            <h2 style={{ margin: 0 }}>Virtual key for {created.name}</h2>
            <button className="btn small" onClick={() => setCreated(null)}>Done</button>
          </div>
          <div className="banner warn mt">Copy it now. KeyLens stores only a hash, so it can't show this again.</div>
          <h3 className="mt">Virtual key</h3>
          <CodeBlock code={created.virtual_key} />
          <div className="grid g3 mt">
            <div><h3>Python (OpenAI SDK)</h3><CodeBlock code={created.snippets.python} /></div>
            <div><h3>curl</h3><CodeBlock code={created.snippets.curl} /></div>
            <div><h3>Environment variables</h3><CodeBlock code={created.snippets.env} /></div>
          </div>
        </div>
      )}

      {apps.length > 0 && (
        <Card title="Your apps" className="mt">
          <div className="scroll">
            <table>
              <thead>
                <tr><th>App</th><th>Key</th><th>Virtual key</th><th className="num">Requests</th><th className="num">Tokens</th>
                  <th className="num">List cost</th><th>Budget (30d)</th><th>Seen from</th><th>Last used</th><th></th></tr>
              </thead>
              <tbody>
                {apps.map((a) => {
                  const used = a.budget_usd ? Math.min(100, (100 * a.month_list_cost) / a.budget_usd) : null;
                  return (
                    <tr key={a.id}>
                      <td><b>{a.name}</b><div className="pill" style={{ marginTop: 3 }}>{a.kind}</div></td>
                      <td>{a.key_label}<div className="mono" style={{ color: "var(--mute)" }}>{a.masked}</div></td>
                      <td className="mono">{a.vkey_hint}</td>
                      <td className="num">{num(a.stats.n)}</td>
                      <td className="num">{compact(a.stats.tokens)}</td>
                      <td className="num">{usd(a.stats.list_cost)}</td>
                      <td style={{ minWidth: 120 }}>
                        {a.budget_usd == null ? <span style={{ color: "var(--mute)" }}>none</span> : (
                          <><div className={`bar ${used! > 90 ? "bad" : used! > 70 ? "warn" : ""}`}><span style={{ width: `${used}%` }} /></div>
                            <div style={{ fontSize: 12, color: "var(--mute)" }}>{usd(a.month_list_cost)} of {usd(a.budget_usd)}</div></>
                        )}
                      </td>
                      <td>{a.sources.map((s: any) => <div key={s.source_class} style={{ fontSize: 12 }}>{SOURCE_LABEL[s.source_class] ?? s.source_class}: {s.n}</div>)}
                        <div style={{ fontSize: 12, color: "var(--mute)" }}>{a.stats.ips} IP{a.stats.ips === 1 ? "" : "s"}</div></td>
                      <td>{ago(a.stats.last_used)}</td>
                      <td>
                        <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                          <button className={`btn small ${a.enabled ? "" : "primary"}`} onClick={() => patch(`/api/apps/${a.id}`, { enabled: !a.enabled }).then(load)}>
                            {a.enabled ? "Pause" : "Resume"}
                          </button>
                          <button className="btn small" onClick={() => rotate(a)}>Rotate</button>
                          <button className="btn small danger" onClick={() => confirm(`Delete ${a.name}?`) && del(`/api/apps/${a.id}`).then(load)}>Delete</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
