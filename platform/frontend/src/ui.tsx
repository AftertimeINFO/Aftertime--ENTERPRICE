import { Component, type ErrorInfo, type ReactNode } from "react";
import { STATUS_VIEW } from "./testsCatalog";

/** A status as an icon, a word and a colour together. */
export function StatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  const view = STATUS_VIEW[status] || { icon: "?", label: status };
  return <span className={`status-badge ${status}`} title={view.label}>
    <i aria-hidden="true">{view.icon}</i>{!compact && <span>{view.label}</span>}
  </span>;
}

/** The last runs of a test, oldest on the left. */
export function Trend({ statuses }: { statuses: string[] }) {
  if (!statuses.length) return <span className="muted">—</span>;
  return <span className="trend" aria-label={`Останні запуски: ${statuses.map((status) => STATUS_VIEW[status]?.label || status).join(", ")}`}>
    {statuses.map((status, index) => <i key={index} className={status} title={STATUS_VIEW[status]?.label || status} />)}
  </span>;
}

export function Kpi({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: string }) {
  return <div className={`kpi ${tone || ""}`}>
    <span>{label}</span>
    <b>{value}</b>
    {hint && <small>{hint}</small>}
  </div>;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty-state"><b>{title}</b>{children && <p>{children}</p>}</div>;
}

/** Proportions of statuses as one bar, with counts readable on hover and for screen readers. */
export function StatusBar({ counts }: { counts: Record<string, number> }) {
  const order = ["passed", "failed", "broken", "skipped", "unknown", "never"];
  const total = order.reduce((sum, key) => sum + (counts[key] || 0), 0);
  if (!total) return null;
  const label = order.filter((key) => counts[key]).map((key) => `${STATUS_VIEW[key].label}: ${counts[key]}`).join(", ");
  return <span className="status-strip" role="img" aria-label={label} title={label}>
    {order.filter((key) => counts[key]).map((key) => <i key={key} className={key} style={{ flexGrow: counts[key] }} />)}
  </span>;
}

/** A failure inside one view stays inside it: the rest of Aftertime keeps working. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Aftertime view failed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return <div className="ux"><EmptyState title="Цей розділ не вдалося показати">
      {this.state.error.message} <button className="link" onClick={() => this.setState({ error: null })}>Спробувати ще раз</button>
    </EmptyState></div>;
  }
}
