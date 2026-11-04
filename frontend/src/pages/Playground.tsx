import { useEffect, useState } from "react";
import { API_BASE, api } from "../api";
import { Card } from "../components/ui";
import { ms, num } from "../format";

/** Calls the KeyLens proxy straight from this browser tab, so the dashboard
 *  records a real browser source (Origin, Sec-Fetch-*, sec-ch-ua headers). */
export default function Playground() {
  const [apps, setApps] = useState<any[]>([]);
  const [vkey, setVkey] = useState("");
  const [model, setModel] = useState("openai/gpt-oss-20b");
  const [models, setModels] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("Explain in one sentence what an API key is.");
  const [stream, setStream] = useState(true);
  const [out, setOut] = useState("");
  const [stats, setStats] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/apps").then(setApps);
    api("/api/keys").then((ks) => {
      const ids = ks.flatMap((k: any) => k.models.map((m: any) => m.id)).filter((m: string) => !/whisper|guard|orpheus|tts/.test(m));
      setModels([...new Set<string>(ids)]);
    });
  }, []);

  const send = async () => {
    setBusy(true);
    setOut("");
    setError("");
    setStats(null);
    const t0 = performance.now();
    try {
      const res = await fetch(`${API_BASE}/v1/chat/completions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${vkey.trim()}`, "Content-Type": "application/json" },
        body: JSON.stringify({ model, stream, max_tokens: 300, messages: [{ role: "user", content: prompt }] }),
      });
      const limits = { left: res.headers.get("x-ratelimit-remaining-requests"), limit: res.headers.get("x-ratelimit-limit-requests") };
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error?.message ?? `HTTP ${res.status}`);
      }
      let usage: any = null;
      if (stream && res.body) {
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.startsWith("data:") || line.includes("[DONE]")) continue;
            try {
              const evt = JSON.parse(line.slice(5));
              const piece = evt.choices?.[0]?.delta?.content;
              if (piece) setOut((o) => o + piece);
              usage = evt.usage ?? evt.x_groq?.usage ?? usage;
            } catch { /* partial line */ }
          }
        }
      } else {
        const body = await res.json();
        setOut(body.choices?.[0]?.message?.content ?? "");
        usage = body.usage;
      }
      setStats({ ms: performance.now() - t0, usage, limits });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Browser playground</h1>
      <div className="sub">This page calls KeyLens from your browser, the way a web app would. The reply is shown here only; KeyLens logs the metadata and tags the source as “Browser”.</div>
      <div className="grid g2">
        <Card>
          <label className="field">Virtual key (create a “browser” app on the Apps page and paste its key)
            <input className="mono" type="password" value={vkey} onChange={(e) => setVkey(e.target.value)} placeholder="kl_live_…" />
          </label>
          {apps.length === 0 && <p className="error">No apps yet. Create one first.</p>}
          <div className="grid g2">
            <label className="field">Model
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {(models.length ? models : [model]).map((m) => <option key={m}>{m}</option>)}
              </select>
            </label>
            <label className="field">Mode
              <select value={stream ? "stream" : "once"} onChange={(e) => setStream(e.target.value === "stream")}>
                <option value="stream">Stream (SSE)</option>
                <option value="once">Single response</option>
              </select>
            </label>
          </div>
          <label className="field">Prompt (sent to the provider, never stored by KeyLens)
            <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </label>
          <button className="btn primary" disabled={busy || !vkey} onClick={send}>{busy ? "Sending…" : "Send from browser"}</button>
          {error && <div className="error">{error}</div>}
        </Card>
        <Card title="Reply">
          <div style={{ whiteSpace: "pre-wrap", minHeight: 120 }}>{out || <span style={{ color: "var(--mute)" }}>The answer appears here.</span>}</div>
          {stats && (
            <div className="kv mt">
              <span className="k">Round trip</span><span>{ms(stats.ms)}</span>
              <span className="k">Tokens</span><span>{num(stats.usage?.prompt_tokens)} in · {num(stats.usage?.completion_tokens)} out</span>
              <span className="k">Daily requests left</span><span>{stats.limits.left ?? "–"} / {stats.limits.limit ?? "–"}</span>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
