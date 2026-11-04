import { useEffect, useState } from "react";
import { api } from "../api";
import { Card, Empty, InsightCard } from "../components/ui";

export default function Insights() {
  const [cards, setCards] = useState<any[] | null>(null);
  useEffect(() => { api("/api/insights").then(setCards); }, []);
  return (
    <>
      <h1>Insights</h1>
      <div className="sub">Plain rules over your metadata. Each card says what was found, how sure KeyLens is, and the next step. No content is read, so model advice is always a hypothesis to test.</div>
      {cards && !cards.length && <Card><Empty icon="✦" title="Nothing to flag yet">Insights appear once traffic flows through your apps.</Empty></Card>}
      <div className="grid g2">
        {cards?.map((c, i) => <InsightCard key={i} c={c} />)}
      </div>
      <Card title="Rules KeyLens checks" className="mt">
        <table>
          <tbody>
            {[
              ["Ghost usage", "Provider rate-limit counter dropped more than KeyLens traffic explains (token-bucket model of the daily limit)."],
              ["Quota forecast", "Last hour's pace vs. the daily request limit and its refill rate."],
              ["Cheaper model", "Median output under 200 tokens on a large model, at least 5 calls; savings computed at list price."],
              ["Hidden reasoning", "Reasoning tokens over 60% of output tokens."],
              ["Queue-bound latency", "Provider queue time over 2x model compute time (Groq reports both)."],
              ["Error spike", "More than 20% failures in the last hour, with the most common error."],
              ["New caller IP", "An app called from an IP not seen in the previous week."],
              ["Free-tier value", "What free-tier traffic would cost at list price."],
              ["Unused key", "A key with no traffic for 7 days."],
            ].map(([k, v]) => <tr key={k}><td style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{k}</td><td style={{ color: "var(--mute)" }}>{v}</td></tr>)}
          </tbody>
        </table>
      </Card>
    </>
  );
}
