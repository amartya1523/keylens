export const num = (n: number | null | undefined) =>
  n == null ? "–" : new Intl.NumberFormat("en-US").format(Math.round(n));

export const compact = (n: number | null | undefined) =>
  n == null ? "–" : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);

/** Dollars with enough precision for tiny per-request costs. */
export function usd(n: number | null | undefined) {
  if (n == null) return "–";
  if (n === 0) return "$0";
  if (Math.abs(n) >= 1) return `$${n.toFixed(2)}`;
  if (Math.abs(n) >= 0.01) return `$${n.toFixed(3)}`;
  if (Math.abs(n) < 0.000001) return "<$0.000001";
  // enough decimals for two significant digits, never scientific notation
  return `$${n.toFixed(Math.min(8, 1 - Math.floor(Math.log10(Math.abs(n)))))}`;
}

/** Short dollar labels for chart axes. */
export const usdAxis = (n: number) => (n === 0 ? "$0" : n >= 0.01 ? `$${n.toFixed(2)}` : `$${(n * 1000).toFixed(2)}m`);

export const ms = (n: number | null | undefined) =>
  n == null ? "–" : n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${Math.round(n)} ms`;

export const pct = (n: number | null | undefined, digits = 0) => (n == null ? "–" : `${n.toFixed(digits)}%`);

export function ago(ts: number | null | undefined) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return `${Math.max(1, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function clock(ts: number, bucket = 3600) {
  const d = new Date(ts * 1000);
  if (bucket >= 86400) return d.toLocaleDateString([], { month: "short", day: "numeric" });
  if (bucket >= 6 * 3600) return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" });
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export const stamp = (ts: number) =>
  new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Airport-style region codes seen in Groq's x-groq-region header (best effort). */
export const REGION: Record<string, string> = {
  bom: "Mumbai", dls: "The Dalles, US", iad: "Ashburn, US", sjc: "San Jose, US",
  dfw: "Dallas, US", hel: "Helsinki", fra: "Frankfurt", lhr: "London", sin: "Singapore",
};

export const SOURCE_LABEL: Record<string, string> = {
  browser: "Browser", sdk: "SDK script", cli: "CLI / API client", script: "HTTP library",
  ide: "IDE / agent", probe: "KeyLens probe", unknown: "Unknown",
};
