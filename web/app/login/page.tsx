"use client";

/**
 * Sign-in page (FR3, FR4, US7): email + password only — no signup path anywhere.
 * Posts to the `/api/auth/login` route handler, which sets the httpOnly cookie;
 * on success we navigate to the chat.
 */
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        setError("Invalid email or password");
        return;
      }
      router.replace("/");
      router.refresh();
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "1.5rem",
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          width: "100%",
          maxWidth: 360,
          background: "var(--wa-panel)",
          border: "1px solid var(--wa-border)",
          borderRadius: 12,
          padding: "2rem",
          display: "flex",
          flexDirection: "column",
          gap: "1rem",
        }}
      >
        <h1 style={{ margin: 0, fontSize: "1.4rem" }}>Water Assistant</h1>
        <p style={{ margin: 0, color: "var(--wa-muted)", fontSize: "0.9rem" }}>
          Sign in with the credentials your administrator provided.
        </p>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: "0.85rem" }}>Email</span>
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={inputStyle}
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: "0.85rem" }}>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={inputStyle}
          />
        </label>

        {error ? (
          <p role="alert" style={{ margin: 0, color: "#f87171", fontSize: "0.85rem" }}>
            {error}
          </p>
        ) : null}

        <button type="submit" disabled={loading} style={buttonStyle}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "0.55rem 0.7rem",
  borderRadius: 8,
  border: "1px solid var(--wa-border)",
  background: "var(--wa-bg)",
  color: "var(--wa-text)",
  fontSize: "0.95rem",
};

const buttonStyle: React.CSSProperties = {
  marginTop: "0.25rem",
  padding: "0.6rem 0.7rem",
  borderRadius: 8,
  border: "none",
  background: "var(--wa-accent)",
  color: "white",
  fontSize: "0.95rem",
  fontWeight: 600,
  cursor: "pointer",
};
