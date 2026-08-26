import type { PropsWithChildren, ReactNode } from "react";
import type { Tone } from "./types";

export const money = (value?: number, signed = false) => value == null ? "—" : new Intl.NumberFormat("pl-PL", { style: "currency", currency: "USD", signDisplay: signed ? "always" : "auto" }).format(value);
export const pct = (value?: number) => value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
export const num = (value?: number, digits = 2) => value == null ? "—" : value.toLocaleString("pl-PL", { maximumFractionDigits: digits });
export const toneFor = (value?: number): Tone => (value || 0) > 0 ? "good" : (value || 0) < 0 ? "bad" : "muted";

export function Pill({ children, tone = "muted" }: PropsWithChildren<{ tone?: Tone }>) {
  return <span className={`pill ${tone}`}>{children}</span>;
}
export function Card({ title, action, className = "", children }: PropsWithChildren<{ title: string; action?: ReactNode; className?: string }>) {
  return <section className={`card ${className}`}><header className="card-head"><h2>{title}</h2>{action}</header><div className="card-body">{children}</div></section>;
}
export function PageHeader({ title, description, context }: { title: string; description: string; context: string }) {
  return <div className="page-head"><div><h1>{title}</h1><p>{description}</p></div><Pill tone="info">{context}</Pill></div>;
}
export function Empty({ children = "Brak danych" }: PropsWithChildren) { return <div className="empty">{children}</div>; }
export function Gate({ gate }: { gate?: string }) {
  const value = gate || "WAIT";
  return <Pill tone={value === "OPEN" ? "good" : value === "BLOCK" ? "bad" : "warn"}>{value === "OPEN" ? "OK" : value === "BLOCK" ? "NA" : "CZEKAJ"}</Pill>;
}
export function Metric({ label, value, detail, tone = "muted" }: { label: string; value: ReactNode; detail?: string; tone?: Tone }) {
  return <div className="metric"><span>{label}</span><strong className={tone}>{value}</strong>{detail && <small>{detail}</small>}</div>;
}
