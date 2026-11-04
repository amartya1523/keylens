import { useState } from "react";
import { post } from "../api";
import { Card, Empty } from "../components/ui";

export default function Scanner() {
  const [path, setPath] = useState("~/Desktop");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setBusy(true);
    setError("");
    try { setRes(await post("/api/scan", { path })); } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <>
      <h1>Key scanner</h1>
      <div className="sub">Where is a key sitting on this machine? Scans a folder plus your shell history for your stored keys and for other LLM key patterns, and checks git tracking and git history. Keys are only ever shown masked.</div>
      <Card>
        <div className="row">
          <input className="mono" style={{ flex: 1 }} value={path} onChange={(e) => setPath(e.target.value)} />
          <button className="btn primary" onClick={run} disabled={busy}>{busy ? "Scanning…" : "Scan"}</button>
        </div>
        <p className="sub" style={{ margin: "8px 0 0", fontSize: 12.5 }}>Runs locally only. Skips node_modules, .git, virtualenvs, binaries and files over 2 MB.</p>
        {error && <div className="error">{error}</div>}
      </Card>
      {res && (
        <Card className="mt" title={`${res.findings.length} finding${res.findings.length === 1 ? "" : "s"} in ${res.files_scanned.toLocaleString()} files`}>
          {!res.findings.length ? <Empty icon="✓" title="No keys found in plain text" /> : (
            <div className="scroll">
              <table>
                <thead><tr><th>Risk</th><th>Where</th><th>Key</th><th>Git</th><th>Fix</th></tr></thead>
                <tbody>
                  {res.findings.map((f: any, i: number) => (
                    <tr key={i}>
                      <td><span className={`pill ${f.risk === "critical" || f.risk === "high" ? "bad" : f.risk === "medium" ? "warn" : "ok"}`}>{f.risk}</span></td>
                      <td className="mono" style={{ wordBreak: "break-all" }}>{f.path}:{f.line}<div style={{ color: "var(--mute)" }}>{f.file_type}</div></td>
                      <td className="mono">{f.masked}<div>{f.yours ? <span className="pill acc">your stored key</span> : <span className="pill">{f.provider}</span>}</div></td>
                      <td style={{ fontSize: 12.5 }}>{f.git_tracked ? "tracked" : "not tracked"}{f.in_history.length ? ` · in ${f.in_history.length} commit(s)` : ""}{f.remote ? <div style={{ color: "var(--mute)" }}>{f.remote}</div> : null}</td>
                      <td style={{ fontSize: 12.5 }}>{f.fix}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
