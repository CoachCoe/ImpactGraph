export function Status({ kind = "pending", children }: { kind?: "verified" | "pending" | "warning" | "failed"; children: React.ReactNode }) {
  const icons = { verified: "✓", pending: "◷", warning: "!", failed: "×" };
  return <span className={`status ${kind}`}><b aria-hidden>{icons[kind]}</b>{children}</span>;
}

