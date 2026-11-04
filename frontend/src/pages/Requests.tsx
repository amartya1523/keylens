import { useEffect, useState } from "react";
import { api } from "../api";
import { Card, Empty } from "../components/ui";
import { API_BASE, getToken } from "../api";
import { REGION, SOURCE_LABEL, ms, num, stamp, usd } from "../format";

export default function Requests() {
  const [events, setEvents] = useState<any[]>([]);
  const [apps, setApps] = useState<any[]>([]);
  const [filter, setFilter] = useState({ app_id: "", source: "", errors_only: false });
  const [detail, setDetail] = useState<any>(null);

  const load = () => {
    const q = new URLSearchParams({ limit: "300" });
    if (filter.app_id) q.set("app_id", filter.app_id);
    if (filter.source) q.set("source", filter.source);
    if (filter.errors_only) q.set("errors_only", "true");
    api(`/api/events?${q}`).then(setEvents);
  };
  useEffect(() => { api("/api/apps").then(setApps); }, []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setDetail(null);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [filter]);

  const exportCsv = async () => {
    const res = await fetch(`${API_BASE}/api/export.csv?range=30d`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) return alert(`Export failed: HTTP ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "keylens-events.csv";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <>
      <div className="row between">
        <div>
          <h1>Request log</h1>
          <div className="sub">Every request that went through KeyLens. Click a row for the raw metadata. There is no prompt or reply text to show: it was never stored.</div>
        </div>
        <button className="btn" onClick={exportCsv}>Export CSV</button>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 200 }} value={filter.app_id} onChange={(e) => setFilter({ ...filter, app_id: e.target.value })}>
          <option value="">All apps</option>
          {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select style={{ width: 180 }} value={filter.source} onChange={(e) => setFilter({ ...filter, source: e.target.value })}>
          <option value="">All sources</option>
          {Object.entries(SOURCE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={filter.errors_only} onChange={(e) => setFilter({ ...filter, errors_only: e.target.checked })} /> errors only
        </label>
      </div>
      <Card>
        {!events.length ? <Empty icon="≡" title={filter.app_id || filter.source || filter.errors_only ? "No requests match these filters" : "No requests yet"} /> : (
          <div className="scroll">
            <table>
              <thead>
                <tr><th>Time</th><th>App</th><th>Source</th><th>Model</th><th className="num">In</th><th className="num">Out</th>
                  <th className="num">Reasoning</th><th className="num">Latency</th><th className="num">Paid</th><th className="num">List</th><th>Status</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="click" onClick={() => api(`/api/events/${e.id}`).then(setDetail)}>
                    <td style={{ whiteSpace: "nowrap" }}>{stamp(e.ts)}</td>
                    <td>{e.app_name ?? <span style={{ color: "var(--mute)" }}>probe</span>}</td>
                    <td><span className="pill">{SOURCE_LABEL[e.source_class] ?? e.source_class}</span><div style={{ fontSize: 11.5, color: "var(--mute)", marginTop: 2 }}>{e.source_detail}</div></td>
                    <td className="mono">{e.model}{e.stream ? " ⇢" : ""}</td>
                    <td className="num">{num(e.prompt_tokens)}</td>
                    <td className="num">{num(e.completion_tokens)}</td>
                    <td className="num">{num(e.reasoning_tokens)}</td>
                    <td className="num">{ms(e.latency_ms)}</td>
                    <td className="num">{usd(e.actual_cost_usd)}</td>
                    <td className="num">{usd(e.list_cost_usd)}</td>
                    <td><span className={`pill ${e.status < 400 ? "ok" : "bad"}`}>{e.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {detail && (
        <div className="drawer-bg" onClick={() => setDetail(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="row between"><h2 style={{ margin: 0 }}>Request #{detail.id}</h2><button className="btn small" onClick={() => setDetail(null)}>Close</button></div>
            <div className="kv mt">
              <span className="k">Time</span><span>{stamp(detail.ts)}</span>
              <span className="k">Provider / endpoint</span><span className="mono">{detail.provider} {detail.endpoint}</span>
              <span className="k">Model</span><span className="mono">{detail.model}</span>
              <span className="k">Source</span><span>{detail.source_detail}</span>
              <span className="k">Caller IP</span><span className="mono">{detail.client_ip ?? "–"}</span>
              <span className="k">Origin</span><span className="mono">{detail.origin ?? "–"}</span>
              <span className="k">User agent</span><span className="mono" style={{ wordBreak: "break-all" }}>{detail.user_agent || "–"}</span>
              <span className="k">Tokens</span><span>{num(detail.prompt_tokens)} in · {num(detail.completion_tokens)} out ({num(detail.reasoning_tokens)} reasoning, {num(detail.cached_tokens)} cached)</span>
              <span className="k">Latency</span><span>{ms(detail.latency_ms)} total{detail.ttfb_ms ? ` · first byte ${ms(detail.ttfb_ms)}` : ""}{detail.queue_ms != null ? ` · queue ${ms(detail.queue_ms)} · compute ${ms(detail.compute_ms)}` : ""}</span>
              <span className="k">Region</span><span>{detail.region ? `${detail.region} (${REGION[detail.region] ?? "unknown"})` : "–"}</span>
              <span className="k">Cost</span><span>paid {usd(detail.actual_cost_usd)} · list {usd(detail.list_cost_usd)} ({detail.raw_meta?.tier ?? "–"} tier)</span>
              <span className="k">Provider request id</span><span className="mono">{detail.upstream_id ?? "–"}</span>
            </div>
            <h3 className="mt2">Raw metadata (exactly what is stored)</h3>
            <div className="code">{JSON.stringify(detail.raw_meta, null, 2)}</div>
          </div>
        </div>
      )}
    </>
  );
}
