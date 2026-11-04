import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api";
import { Card, COLORS, Empty, HeadroomBar, InsightCard, Kpi, Seg } from "../components/ui";
import { REGION, SOURCE_LABEL, clock, compact, ms, num, pct, usd } from "../format";

type Range = "1h" | "24h" | "7d" | "30d";

export default function Overview() {
  const [range, setRange] = useState<Range>("24h");
  const [keyId, setKeyId] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [keys, setKeys] = useState<any[]>([]);
  const [insights, setInsights] = useState<any[]>([]);
  const [where, setWhere] = useState<any[]>([]);

  const load = () => {
    const q = `range=${range}${keyId ? `&key_id=${keyId}` : ""}`;
    api(`/api/overview?${q}`).then(setData);
    api(`/api/where?${q}`).then(setWhere);
    api("/api/insights").then(setInsights);
  };
  useEffect(() => { api("/api/keys").then(setKeys); }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [range, keyId]);

  if (!data) return null;
  const k = data.kpis;
  const tick = (t: number) => clock(t, data.bucket_s);
  const tooltipLabel = (t: any) => clock(Number(t), data.bucket_s);

  if (!keys.length)
    return (
      <>
        <h1>Overview</h1>
        <Card>
          <Empty icon="⚿" title="Add your first API key">
            <p>Paste a Groq, xAI, OpenAI, Anthropic or OpenRouter key. KeyLens detects the provider and checks it for free.</p>
            <Link className="btn primary" to="/keys">Add a key</Link>
          </Empty>
        </Card>
      </>
    );

  const delta = k.requests_prev ? Math.round(((k.requests - k.requests_prev) / k.requests_prev) * 100) : null;

  return (
    <>
      <div className="row between">
        <div>
          <h1>Where your keys are used</h1>
          <div className="sub">Live from the KeyLens proxy · refreshes every 15 s · prices: LiteLLM snapshot {data.pricing.fetched}</div>
        </div>
        <div className="row">
          <select value={keyId} onChange={(e) => setKeyId(e.target.value)} style={{ width: 220 }}>
            <option value="">All keys</option>
            {keys.map((x) => <option key={x.id} value={x.id}>{x.label} · {x.masked}</option>)}
          </select>
          <Seg value={range} options={["1h", "24h", "7d", "30d"] as Range[]} onChange={setRange} />
        </div>
      </div>

      <div className="grid g6">
        <Kpi label="Requests" value={num(k.requests)} hint={delta == null ? `${k.apps} apps · ${k.sources} source types` : `${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta)}% vs previous`} />
        <Kpi label="Tokens" value={compact(k.tokens_in + k.tokens_out)} hint={`${compact(k.tokens_in)} in · ${compact(k.tokens_out)} out · ${compact(k.tokens_reasoning)} reasoning`} />
        <Kpi label="You paid" value={usd(k.actual_cost)} hint={<>at list price: <b>{usd(k.list_cost)}</b></>} />
        <Kpi label="Latency p50" value={ms(k.latency_p50)} hint={`p95 ${ms(k.latency_p95)} · errors ${pct(k.error_rate * 100, 1)}`} />
        <Kpi label="Daily quota left" value={k.headroom_pct == null ? "–" : pct(k.headroom_pct, 1)} hint={k.headroom_pct == null ? "run a probe on the Keys page" : <HeadroomBar pct={k.headroom_pct} />} />
        <Kpi label="Ghost requests (24h)" value={num(k.ghost_requests)} alert={k.ghost_requests > 0} hint={k.ghost_requests > 0 ? "used outside KeyLens" : "nothing outside KeyLens"} />
      </div>

      {insights.length > 0 && (
        <div className="grid g3 mt">
          {insights.slice(0, 3).map((c, i) => <InsightCard key={i} c={c} />)}
        </div>
      )}

      {k.requests === 0 ? (
        <Card className="mt">
          <Empty icon="▦" title="No traffic in this range yet">
            <p>Create an app on the <Link to="/apps">Apps</Link> page, point its base URL at KeyLens, or try the <Link to="/playground">browser playground</Link>.</p>
          </Empty>
        </Card>
      ) : (
        <>
          <div className="grid g2 mt">
            <Card title="Requests by app">
              <ResponsiveContainer width="100%" height={230}>
                <AreaChart data={data.app_series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                  <XAxis dataKey="t" tickFormatter={tick} fontSize={11} stroke="var(--mute)" />
                  <YAxis allowDecimals={false} fontSize={11} stroke="var(--mute)" width={30} />
                  <Tooltip labelFormatter={tooltipLabel} />
                  <Legend iconSize={9} wrapperStyle={{ fontSize: 12 }} />
                  {data.app_names.map((n: string, i: number) => (
                    <Area key={n} type="monotone" dataKey={n} stackId="1" stroke={COLORS[i % 8]} fill={COLORS[i % 8]} fillOpacity={0.35} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Tokens by type">
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={data.series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                  <XAxis dataKey="t" tickFormatter={tick} fontSize={11} stroke="var(--mute)" />
                  <YAxis fontSize={11} stroke="var(--mute)" width={40} tickFormatter={compact} />
                  <Tooltip labelFormatter={tooltipLabel} />
                  <Legend iconSize={9} wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="tokens_in" name="input" stackId="t" fill={COLORS[0]} />
                  <Bar dataKey="tokens_out" name="visible output" stackId="t" fill={COLORS[2]} />
                  <Bar dataKey="reasoning" name="reasoning" stackId="t" fill={COLORS[1]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          </div>

          <div className="grid g3 mt">
            <Card title="Cost: paid vs list price">
              <ResponsiveContainer width="100%" height={210}>
                <LineChart data={data.series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                  <XAxis dataKey="t" tickFormatter={tick} fontSize={11} stroke="var(--mute)" />
                  <YAxis fontSize={11} stroke="var(--mute)" width={52} tickFormatter={(v) => usd(v)} />
                  <Tooltip labelFormatter={tooltipLabel} formatter={(v: any) => usd(Number(v))} />
                  <Legend iconSize={9} wrapperStyle={{ fontSize: 12 }} />
                  <Line type="monotone" dataKey="list_cost" name="list price" stroke={COLORS[1]} dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="actual_cost" name="you paid" stroke={COLORS[0]} dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Where calls come from">
              <ResponsiveContainer width="100%" height={210}>
                <BarChart data={data.by_source.map((s: any) => ({ ...s, name: SOURCE_LABEL[s.name] ?? s.name }))} layout="vertical" margin={{ left: 10 }}>
                  <XAxis type="number" allowDecimals={false} fontSize={11} stroke="var(--mute)" />
                  <YAxis type="category" dataKey="name" width={110} fontSize={11} stroke="var(--mute)" />
                  <Tooltip />
                  <Bar dataKey="requests" fill={COLORS[0]} radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Model mix (tokens)">
              <ResponsiveContainer width="100%" height={210}>
                <PieChart>
                  <Pie data={data.by_model} dataKey="tokens" nameKey="name" innerRadius={48} outerRadius={78} paddingAngle={2}>
                    {data.by_model.map((_: any, i: number) => <Cell key={i} fill={COLORS[i % 8]} />)}
                  </Pie>
                  <Tooltip formatter={(v: any) => num(Number(v))} />
                  <Legend iconSize={9} wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            </Card>
          </div>

          <div className="grid g2 mt">
            <Card title="Latency breakdown per model (median)">
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={data.latency_by_model} layout="vertical" margin={{ left: 10 }}>
                  <XAxis type="number" fontSize={11} stroke="var(--mute)" unit=" ms" />
                  <YAxis type="category" dataKey="model" width={150} fontSize={11} stroke="var(--mute)" />
                  <Tooltip formatter={(v: any) => ms(Number(v))} />
                  <Legend iconSize={9} wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="queue_ms" name="waiting in provider queue" stackId="l" fill={COLORS[1]} />
                  <Bar dataKey="compute_ms" name="model compute" stackId="l" fill={COLORS[2]} />
                  <Bar dataKey="other_ms" name="network + KeyLens" stackId="l" fill={COLORS[6]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Rate-limit headroom (from provider headers)">
              {data.limits.length === 0 ? (
                <p className="sub">No rate-limit headers seen yet.</p>
              ) : (
                <table>
                  <thead><tr><th>Model</th><th>Requests today</th><th className="num">Left</th><th>Tokens / min</th></tr></thead>
                  <tbody>
                    {data.limits.map((s: any) => (
                      <tr key={s.id}>
                        <td className="mono">{s.model}</td>
                        <td style={{ width: "35%" }}>
                          {s.limit_requests ? <HeadroomBar pct={(100 * s.remaining_requests) / s.limit_requests} /> : "–"}
                        </td>
                        <td className="num">{num(s.remaining_requests)} / {num(s.limit_requests)}</td>
                        <td className="num">{num(s.remaining_tokens)} / {num(s.limit_tokens)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>

          <Card title="Where is the key used? (key → app → client → IP)" className="mt">
            <WhereFlow rows={where} />
          </Card>

          <div className="grid g3 mt">
            <Breakdown title="Top clients" rows={data.by_source_detail} />
            <Breakdown title="Caller IPs" rows={data.by_ip} />
            <Breakdown title="Provider region" rows={data.by_region.map((r: any) => ({ ...r, name: r.name === "unknown" ? r.name : `${r.name} · ${REGION[r.name] ?? "?"}` }))} />
          </div>
        </>
      )}
    </>
  );
}

function Breakdown({ title, rows }: { title: string; rows: any[] }) {
  return (
    <Card title={title}>
      <table>
        <tbody>
          {rows.slice(0, 6).map((r) => (
            <tr key={r.name}>
              <td style={{ wordBreak: "break-word" }}>{r.name}</td>
              <td className="num">{num(r.requests)}</td>
              <td className="num" style={{ color: "var(--mute)" }}>{usd(r.list_cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function WhereFlow({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="sub">No traffic yet.</p>;
  const sum = (field: string) => {
    const m = new Map<string, number>();
    rows.forEach((r) => m.set(r[field], (m.get(r[field]) ?? 0) + r.requests));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };
  const cols: [string, string][] = [["key", "Key"], ["app", "App"], ["source", "Client"], ["ip", "Caller IP"]];
  return (
    <div className="flow">
      {cols.map(([field, label]) => (
        <div className="col" key={field}>
          <h3>{label}</h3>
          {sum(field).map(([name, n]) => (
            <div className="node" key={name}>
              {name} <b style={{ float: "right" }}>{n}</b>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
