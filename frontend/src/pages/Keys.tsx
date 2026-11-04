import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, del, patch, post } from "../api";
import { Card, COLORS, Empty, HeadroomBar, Info } from "../components/ui";
import { ago, clock, compact, num, usd } from "../format";

const PREFIXES: [RegExp, string][] = [
  [/^gsk_/, "Groq"], [/^xai-/, "xAI (Grok)"], [/^sk-or-/, "OpenRouter"], [/^sk-ant-/, "Anthropic"],
  [/^sk-[a-f0-9]{32}$/, "DeepSeek"], [/^sk-/, "OpenAI"],
];

export default function Keys() {
  const [keys, setKeys] = useState<any[]>([]);
  const [raw, setRaw] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = () => api("/api/keys").then(setKeys);
  useEffect(() => { load(); }, []);
  const guess = PREFIXES.find(([re]) => re.test(raw.trim()))?.[1];

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const res = await post("/api/keys", { key: raw.trim(), label: label || undefined });
      setMsg({ ok: res.check.ok, text: res.check.ok
        ? `${res.key.provider_name} key works: ${res.key.models.length} models available${res.check.region ? `, served from ${res.check.region}` : ""}.`
        : `Saved, but the provider rejected it: ${res.check.error}` });
      setRaw("");
      setLabel("");
      load();
    } catch (err: any) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>API keys</h1>
      <div className="sub">Paste a provider key. It is encrypted at rest and never shown again; KeyLens only displays a masked version.</div>

      <Card>
        <form onSubmit={add} className="grid" style={{ gridTemplateColumns: "2fr 1fr auto", alignItems: "end" }}>
          <label className="field" style={{ margin: 0 }}>Provider API key
            <input className="mono" type="password" placeholder="gsk_… · xai-… · sk-… · sk-ant-… · sk-or-…" value={raw} onChange={(e) => setRaw(e.target.value)} required autoComplete="off" />
          </label>
          <label className="field" style={{ margin: 0 }}>Label (optional)
            <input placeholder="e.g. Groq team key" value={label} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <button className="btn primary" disabled={busy || !raw}>{busy ? "Checking…" : "Add & check"}</button>
        </form>
        <div className="row mt" style={{ minHeight: 22 }}>
          {raw && <span className="pill acc">{guess ? `Detected: ${guess}` : "Unknown provider"}</span>}
          {msg && <span style={{ color: msg.ok ? "var(--ok)" : "var(--bad)" }}>{msg.text}</span>}
        </div>
        <p className="sub" style={{ margin: "8px 0 0", fontSize: 12.5 }}>
          The check is free: it lists models and reads the provider's own key-info endpoint where one exists (xAI, OpenRouter, DeepSeek).
        </p>
      </Card>

      {!keys.length && <Card className="mt"><Empty icon="⚿" title="No keys yet">Paste one above to start.</Empty></Card>}

      {keys.map((k) => (
        <KeyCard key={k.id} k={k} open={open === k.id} toggle={() => setOpen(open === k.id ? null : k.id)} reload={load} />
      ))}
    </>
  );
}

function KeyCard({ k, open, toggle, reload }: { k: any; open: boolean; toggle: () => void; reload: () => void }) {
  const [busy, setBusy] = useState("");
  const [ghost, setGhost] = useState<any>(null);
  const valid = k.status === "valid";

  useEffect(() => { if (open) api(`/api/keys/${k.id}/ghost`).then(setGhost); }, [open, k.stats.n]);

  const act = async (name: string, fn: () => Promise<any>) => {
    setBusy(name);
    try { await fn(); } catch (e: any) { alert(e.message); } finally { setBusy(""); reload(); }
  };

  return (
    <Card className="mt">
      <div className="row between">
        <div className="row">
          <span className={`dot ${valid ? "ok" : "bad"}`} />
          <div>
            <div style={{ fontWeight: 600 }}>{k.label} <span className="pill">{k.provider_name}</span></div>
            <div className="mono" style={{ color: "var(--mute)" }}>{k.masked} · fingerprint {k.fingerprint.slice(0, 8)} · checked {ago(k.last_checked)}</div>
          </div>
        </div>
        <div className="row">
          <select value={k.plan} style={{ width: 130 }} onChange={(e) => act("plan", () => patch(`/api/keys/${k.id}`, { plan: e.target.value }))} title="Used to decide if you actually pay">
            <option value="auto">Plan: auto</option>
            <option value="free">Plan: free tier</option>
            <option value="paid">Plan: paid</option>
          </select>
          <button className="btn small" disabled={!!busy} onClick={() => act("check", () => post(`/api/keys/${k.id}/check`))}>{busy === "check" ? "…" : "Re-check"}</button>
          <button className="btn small" disabled={!!busy || !valid} title="Sends one 1-token request to read rate-limit headers" onClick={() => act("probe", () => post(`/api/keys/${k.id}/probe`))}>{busy === "probe" ? "…" : "Probe limits"}</button>
          <button className="btn small" onClick={toggle}>{open ? "Hide" : "Details"}</button>
          <button className="btn small danger" onClick={() => confirm(`Remove ${k.label} from KeyLens? Its apps stop working.`) && act("del", () => del(`/api/keys/${k.id}`))}>Remove</button>
        </div>
      </div>

      <div className="grid g4 mt">
        <Stat label="Requests via KeyLens" value={num(k.stats.n)} />
        <Stat label="Tokens via KeyLens" value={compact(k.stats.tokens)} />
        <Stat label="Paid / list price" value={`${usd(k.stats.actual)} / ${usd(k.stats.list_cost)}`} />
        <Stat label="Last used via KeyLens" value={ago(k.stats.last_used)} />
      </div>
      <p className="sub" style={{ marginTop: 6 }}>
        These numbers count only requests sent through this account's virtual keys, from the moment the key was added.
        {k.provider_name}'s own console counts every call made with the key, from any client, so it will usually be higher.
        {k.stats.n === 0 && " To see traffic here, create an app on Apps & virtual keys and point it at KeyLens."}
      </p>

      {k.limits.length > 0 && (
        <div className="mt">
          <h3>Rate limits (latest provider headers)</h3>
          <table>
            <thead><tr><th>Model</th><th>Daily requests left</th><th className="num">Remaining</th><th className="num">Tokens/min</th><th>Seen</th></tr></thead>
            <tbody>
              {k.limits.map((s: any) => (
                <tr key={s.id}>
                  <td className="mono">{s.model}</td>
                  <td style={{ width: "30%" }}>{s.limit_requests ? <HeadroomBar pct={(100 * s.remaining_requests) / s.limit_requests} /> : "–"}</td>
                  <td className="num">{num(s.remaining_requests)} / {num(s.limit_requests)}</td>
                  <td className="num">{num(s.remaining_tokens)} / {num(s.limit_tokens)}</td>
                  <td>{ago(s.ts)} · {s.via}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {k.forecast && <p className="sub" style={{ marginTop: 6 }}>Forecast: {k.forecast.message}</p>}
        </div>
      )}

      {open && (
        <div className="mt2">
          <div className="grid g2">
            <div>
              <h3>What the provider tells us about this key</h3>
              <ul style={{ margin: 0, paddingLeft: 18, color: "var(--mute)" }}>
                {k.notes.map((n: string) => <li key={n}>{n}</li>)}
              </ul>
              {Object.keys(k.key_info || {}).length > 0 && (
                <div className="code mt">{JSON.stringify(k.key_info, null, 2)}</div>
              )}
              <h3 className="mt">Models this key can call ({k.models.length})</h3>
              <div className="row" style={{ gap: 6 }}>
                {k.models.map((m: any) => <span className="pill" key={m.id} title={m.owned_by}>{m.id}</span>)}
              </div>
            </div>
            <div>
              <h3>Ghost usage: provider counter vs KeyLens traffic (24h) <Info text="The provider's daily request bucket refills continuously (limit / 86,400 per second). KeyLens simulates the same bucket with only the requests it proxied. If the provider's bucket is emptier, the difference was used somewhere else." /></h3>
              {!ghost || !Object.keys(ghost).length ? (
                <p className="sub">Needs rate-limit headers. Send traffic through an app or press “Probe limits”.</p>
              ) : (
                Object.entries(ghost).map(([model, g]: any) => (
                  <div key={model} style={{ marginBottom: 14 }}>
                    <div className="row between">
                      <span className="mono">{model}</span>
                      <span className={`pill ${g.ghost_requests ? "bad" : "ok"}`}>
                        {g.ghost_requests ? `~${g.ghost_requests} requests outside KeyLens` : "all usage accounted for"}
                      </span>
                    </div>
                    <ResponsiveContainer width="100%" height={150}>
                      <AreaChart data={g.points}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                        <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickCount={5}
                          tickFormatter={(t) => clock(t, 300)} fontSize={10} stroke="var(--mute)" />
                        <YAxis fontSize={10} stroke="var(--mute)" width={30} />
                        <Tooltip labelFormatter={(t) => clock(Number(t), 300)} />
                        <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                        <Area type="stepAfter" dataKey="provider_used" name="provider says used" stroke={COLORS[3]} fill={COLORS[3]} fillOpacity={0.15} />
                        <Area type="stepAfter" dataKey="keylens_saw" name="KeyLens saw" stroke={COLORS[0]} fill={COLORS[0]} fillOpacity={0.2} />
                      </AreaChart>
                    </ResponsiveContainer>
                    {g.note && <p className="sub" style={{ fontSize: 12 }}>{g.note}</p>}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card tight kpi" style={{ background: "var(--soft)" }}>
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 18 }}>{value}</div>
    </div>
  );
}
