import { useState } from "react";
import { post, setToken } from "../api";
import type { User } from "../App";

export default function Auth({ onAuth }: { onAuth: (u: User) => void }) {
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = mode === "signup" ? form : { email: form.email, password: form.password };
      const res = await post<{ token: string; user: User }>(`/api/auth/${mode}`, body);
      setToken(res.token);
      onAuth(res.user);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <form className="card" onSubmit={submit}>
        <div className="brand" style={{ padding: 0, marginBottom: 6 }}>
          <img src="/favicon.svg" alt="" style={{ width: 28 }} /> KeyLens
        </div>
        <p className="sub">See where your LLM API keys are used. Metadata only, never prompts.</p>
        <div className="seg" style={{ marginBottom: 16 }}>
          <button type="button" className={mode === "signup" ? "on" : ""} onClick={() => setMode("signup")}>Create account</button>
          <button type="button" className={mode === "login" ? "on" : ""} onClick={() => setMode("login")}>Sign in</button>
        </div>
        {mode === "signup" && (
          <label className="field">Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </label>
        )}
        <label className="field">Email
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        </label>
        <label className="field">Password (8+ characters)
          <input type="password" minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="btn primary mt" style={{ width: "100%" }} disabled={busy}>
          {busy ? "…" : mode === "signup" ? "Create account" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
