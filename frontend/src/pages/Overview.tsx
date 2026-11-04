import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Layer, Legend, Line, LineChart, Pie, PieChart,
  Rectangle, ResponsiveContainer, Sankey, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api";
import { Card, COLORS, Empty, HeadroomBar, InsightCard, Kpi, Seg } from "../components/ui";
import { REGION, SOURCE_LABEL, clock, compact, ms, num, pct, usd, usdAxis } from "../format";

type Range = "1h" | "24h" | "7d" | "30d";

export default function Overview() {
  const [range, setRange] = useState<Range>("24h");
  const [keyId, setKeyId] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [keys, setKeys] = useState<any[] | null>(null);
  const [apps, setApps] = useState<any[]>([]);
  const [insights, setInsights] = useState<any[]>([]);
  const [where, setWhere] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const q = `range=${range}${keyId ? `&key_id=${keyId}` : ""}`;
    const load = () =>
      Promise.all([api(`/api/overview?${q}`), api(`/api/where?${q}`), api("/api/insights"), api("/api/keys"), api("/api/apps")])
        .then(([o, w, i, ks, as]) => {
          if (!alive) return; // a newer range/key selection is already loading
          setData(o); setWhere(w); setInsights(i); setKeys(ks); setApps(as); setError("");
        })
        .catch((e) => alive && setError(e.message));
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, [range, keyId]);

  if (error && !data) return <Card><Empty icon="!" title="Could not load the dashboard">{error}. Is the backend running on port 8000?</Empty></Card>;
  if (!data || !keys) return <div className="sub">Loading…</div>;
  const k = data.kpis;
  const tick = (t: number) => clock(t, data.bucket_s);
  const tooltipLabel = (t: any) => clock(Number(t), data.bucket_s);
  const steps = [
    { done: keys.length > 0, title: "Add a provider key", body: "Paste a Groq, xAI, OpenAI, Anthropic or OpenRouter key.", to: "/keys" },
    { done: apps.length > 0, title: "Create an app", body: "Each app gets its own virtual key and base URL.", to: "/apps" },
    { done: k.requests > 0 || keys.some((x) => x.stats.n > 0), title: "Send a request", body: "Use the snippet, the demo script, or the browser playground.", to: "/playground" },
  ];
  const onboarding = steps.some((x) => !x.done);
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

      {onboarding && (
        <Card title="Get started" className="" right={<span className="sub" style={{ margin: 0 }}>{steps.filter((x) => x.done).length} of 3 done</span>}>
          <div className="steps">
            {steps.map((st, i) => (
              <Link key={st.title} to={st.to} className={`step${st.done ? " done" : ""}`} style={{ color: "var(--ink)" }}>
                <div style={{ fontWeight: 600 }}><span className="n">{st.done ? "✓" : i + 1}</span>{st.title}</div>
                <div style={{ color: "var(--mute)", fontSize: 12.5, marginTop: 4 }}>{st.body}</div>
              </Link>
            ))}
          </div>
        </Card>
      )}

      <div className={`grid g6${onboarding ? " mt" : ""}`}>
        <Kpi label="Requests" value={num(k.requests)} hint={delta == null ? `${k.apps} apps · ${k.sources} source types` : `${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta)}% vs previous`} />
        <Kpi label="Tokens" value={compact(k.tokens_in + k.tokens_out)} hint={`${compact(k.tokens_in)} in · ${compact(k.tokens_out)} out · ${compact(k.tokens_reasoning)} reasoning`} />
        {k.list_cost > k.actual_cost ? (
          <Kpi good label="Free tier saved you" value={usd(k.list_cost - k.actual_cost)} hint={<>you paid {usd(k.actual_cost)}</>}
            info="List price = what this traffic costs at the provider's public per-token rate (LiteLLM price list). On a free tier you pay $0." />
        ) : (
          <Kpi label="You paid" value={usd(k.actual_cost)} hint={<>at list price: <b>{usd(k.list_cost)}</b></>}
            info="Cost computed from token counts and the provider's public per-token price." />
        )}
        <Kpi label="Latency p50" value={ms(k.latency_p50)} hint={`p95 ${ms(k.latency_p95)} · errors ${pct(k.error_rate * 100, 1)}`} />
        <Kpi label="Tightest daily quota" value={k.headroom_pct == null ? "–" : `${pct(k.headroom_pct, 1)} left`}
          hint={k.headroom_pct == null ? "run a probe on the Keys page" : <HeadroomBar pct={k.headroom_pct} />}
          info="Lowest 'requests left today' across your keys and models, read from the provider's rate-limit headers." />
        <Kpi label="Coverage (24h)" value={k.coverage_pct == null ? "–" : pct(k.coverage_pct, 0)} alert={k.ghost_requests > 0}
          hint={k.ghost_requests > 0 ? `~${num(k.ghost_requests)} requests went around KeyLens` : "every counted request went through KeyLens"}
          info="Share of the provider-counted requests that KeyLens actually saw. Ghost requests = counted by the provider's rate-limit bucket but never proxied: another app, another key in the org, or a leak." />
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
                    <Area key={n} type="monotone" name={n} dataKey={(row: any) => row[n] ?? 0} stackId="1" stroke={COLORS[i % 8]} fill={COLORS[i % 8]} fillOpacity={0.35} />
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
                  <YAxis fontSize={11} stroke="var(--mute)" width={52} tickFormatter={usdAxis} />
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

          <Card title="Where is the key used? key → app → client → caller IP" className="mt"
            right={<span className="sub" style={{ margin: 0 }}>width = requests · red = counted by the provider but never seen by KeyLens</span>}>
            <WhereSankey rows={where} ghost={k.ghost_requests} />
          </Card>

          <div className="grid g3 mt">
            <Breakdown title="Top clients" rows={data.by_source_detail} />
            <Breakdown title="Caller IPs" rows={data.by_ip} />
            <Breakdown title="Provider region" rows={data.by_region.map((r: any) => ({ ...r, name: r.name === "unknown" ? r.name : REGION[r.name] ? `${r.name} · ${REGION[r.name]}` : r.name }))} />
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

const COL_COLORS = ["#4f46e5", "#0d9488", "#f97316", "#7c3aed"];

function WhereSankey({ rows, ghost }: { rows: any[]; ghost: number }) {
  if (!rows.length) return <p className="sub">No traffic yet.</p>;
  const cols = ["key", "app", "source", "ip"];
  const nodes: { name: string; col: number; ghost?: boolean }[] = [];
  const index = new Map<string, number>();
  const node = (col: number, name: string, isGhost = false) => {
    const id = `${col}:${name}`;
    if (!index.has(id)) { index.set(id, nodes.length); nodes.push({ name, col, ghost: isGhost }); }
    return index.get(id)!;
  };
  const links = new Map<string, number>();
  const link = (a: number, b: number, v: number) => links.set(`${a}>${b}`, (links.get(`${a}>${b}`) ?? 0) + v);
  for (const r of rows) {
    const ids = cols.map((c, i) => node(i, String(r[c] ?? "unknown")));
    for (let i = 0; i < ids.length - 1; i++) link(ids[i], ids[i + 1], r.requests);
  }
  if (ghost > 0) {
    const keyNode = node(0, rows[0].key);
    link(keyNode, node(1, "Outside KeyLens", true), ghost);
  }
  const data = {
    nodes,
    links: [...links.entries()].map(([k, value]) => { const [source, target] = k.split(">").map(Number); return { source, target, value }; }),
  };
  const height = Math.max(240, Math.min(520, nodes.length * 30));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Sankey data={data} nodePadding={18} nodeWidth={10} margin={{ left: 10, right: 190, top: 10, bottom: 10 }}
        link={{ stroke: "#94a3b8", strokeOpacity: 0.35 }} node={<SankeyNode nodes={nodes} />}>
        <Tooltip formatter={(v: any) => `${num(Number(v))} requests`} />
      </Sankey>
    </ResponsiveContainer>
  );
}

function SankeyNode({ x, y, width, height, index, payload, nodes }: any) {
  const meta = nodes[index] ?? {};
  const fill = meta.ghost ? "#dc2626" : COL_COLORS[meta.col] ?? "#4f46e5";
  const label = String(payload.name).length > 34 ? String(payload.name).slice(0, 33) + "…" : payload.name;
  return (
    <Layer>
      <Rectangle x={x} y={y} width={width} height={height} fill={fill} fillOpacity={0.9} />
      <text x={x + width + 6} y={y + height / 2} dy="0.35em" fontSize={11.5} fill={meta.ghost ? "#dc2626" : "currentColor"}
        fontWeight={meta.ghost ? 700 : 500}>
        {label} <tspan fill="#94a3b8">{num(payload.value)}</tspan>
      </text>
    </Layer>
  );
}
