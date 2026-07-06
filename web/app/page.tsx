/**
 * Home. The route guard in `middleware.ts` (added in T019) sends unauthenticated
 * visitors to `/login`; a signed-in user lands here. The chat pane replaces this
 * placeholder in T021.
 */
export default function HomePage() {
  return (
    <main style={{ maxWidth: 640, margin: "4rem auto", padding: "0 1.5rem" }}>
      <h1>Water Assistant</h1>
      <p style={{ color: "var(--wa-muted)" }}>
        Frontend scaffold is up. Sign-in and chat land in the following tasks.
      </p>
    </main>
  );
}
