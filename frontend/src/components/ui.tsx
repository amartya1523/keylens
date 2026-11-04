import { useState, type ReactNode } from "react";

export const COLORS = ["#4f46e5", "#f97316", "#0d9488", "#db2777", "#65a30d", "#7c3aed", "#0284c7", "#ca8a04"];

export function Kpi({ label, value, hint, alert }: { label: string; value: ReactNode; hint?: ReactNode; alert?: boolean }) {
  return (
    <div className={`card tight kpi${alert ? " alert" : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Card({ title, right, children, className = "" }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`card ${className}`}>
      {(title || right) && (
        <div className="row between" style={{ marginBottom: 10 }}>
          {title && <h2 style={{ margin: 0 }}>{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: T[]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o} className={o === value ? "on" : ""} onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  );
}

export function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn small copy"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        });
      }}
    >
      {done ? "Copied" : "Copy"}
    </button>
  );
}

export function CodeBlock({ code }: { code: string }) {
  return (
    <div className="code">
      <Copy text={code} />
      {code}
    </div>
  );
}

export function Empty({ icon = "◌", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="big">{icon}</div>
      <div style={{ fontWeight: 600, color: "var(--ink)", marginBottom: 4 }}>{title}</div>
      {children}
    </div>
  );
}

export function InsightCard({ c }: { c: any }) {
  return (
    <div className={`card tight insight ${c.severity}`}>
      <div className="title">{c.title}</div>
      <div style={{ color: "var(--mute)" }}>{c.body}</div>
      <div className="meta">
        <span className={`pill ${c.severity === "critical" ? "bad" : c.severity === "warning" ? "warn" : c.severity === "success" ? "ok" : "acc"}`}>
          {c.severity}
        </span>{" "}
        confidence: {c.confidence} · next step: {c.action}
      </div>
    </div>
  );
}

export function HeadroomBar({ pct }: { pct: number }) {
  const cls = pct < 10 ? "bad" : pct < 30 ? "warn" : "";
  return (
    <div className={`bar ${cls}`} title={`${pct.toFixed(1)}% left`}>
      <span style={{ width: `${Math.max(2, Math.min(100, pct))}%` }} />
    </div>
  );
}
