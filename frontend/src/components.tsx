import { useEffect, useRef, type PropsWithChildren, type ReactNode } from "react";
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

export function ConfirmDialog({ open, title, description, confirmLabel, busy = false, onConfirm, onCancel }: {
  open: boolean; title: string; description: string; confirmLabel: string; busy?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onCancel(); };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previous?.focus(); };
  }, [busy, onCancel, open]);
  if (!open) return null;
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
      <h2 id="confirm-title">{title}</h2><p id="confirm-description">{description}</p>
      <div className="dialog-actions"><button ref={cancelRef} type="button" disabled={busy} onClick={onCancel}>Anuluj</button><button className="btn danger" type="button" disabled={busy} onClick={onConfirm}>{confirmLabel}</button></div>
    </section>
  </div>;
}
