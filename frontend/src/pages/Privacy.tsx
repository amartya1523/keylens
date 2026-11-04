import { useEffect, useState } from "react";
import { api } from "../api";
import { Card } from "../components/ui";

export default function Privacy() {
  const [meta, setMeta] = useState<any>(null);
  useEffect(() => { api("/api/meta").then(setMeta); }, []);
  if (!meta) return null;
  return (
    <>
      <h1>Privacy</h1>
      <div className="sub">KeyLens is metadata-only by construction: no table has a column for prompt or reply text, and a canary test fails the build if any text reaches the database.</div>
      <div className="grid g2">
        <Card title="Stored">
          <ul style={{ margin: 0, paddingLeft: 18 }}>{meta.privacy.stored.map((s: string) => <li key={s}>{s}</li>)}</ul>
        </Card>
        <Card title="Never stored">
          <ul style={{ margin: 0, paddingLeft: 18 }}>{meta.privacy.never_stored.map((s: string) => <li key={s}>{s}</li>)}</ul>
        </Card>
      </div>
      <Card title="How each provider shares usage data" className="mt">
        <table>
          <tbody>
            {meta.providers.map((p: any) => (
              <tr key={p.id}><td style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{p.name}</td>
                <td><ul style={{ margin: 0, paddingLeft: 18, color: "var(--mute)" }}>{p.notes.map((n: string) => <li key={n}>{n}</li>)}</ul></td></tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Prove it" className="mt">
        <p>Run the canary test: it sends a unique prompt through the proxy and asserts the string appears nowhere in the database.</p>
        <div className="code">cd backend && .venv/bin/python tests/test_privacy.py</div>
        <p className="sub mt" style={{ fontSize: 12.5 }}>Prices: {meta.pricing.models} models from the LiteLLM price list, snapshot {meta.pricing.fetched}.</p>
      </Card>
    </>
  );
}
