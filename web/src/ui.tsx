import { useEffect, useRef, type ReactNode } from "react";

/** A modal built on <dialog>, so focus trapping and Escape come for free. */
export function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-label={title}>
      {open && (
        <>
          <header>
            <h2>{title}</h2>
            <button type="button" className="icon" aria-label="Close" onClick={onClose}>
              ×
            </button>
          </header>
          {children}
        </>
      )}
    </dialog>
  );
}

export function money(value: string | null | undefined, currency: string, perHour = false) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  let s: string;
  try {
    s = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: n % 1 === 0 ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    s = `${value} ${currency}`;
  }
  return perHour ? `${s}/h` : s;
}

/** Form input value ("" means unset) → API value (null). */
export const orNull = (v: string) => (v.trim() === "" ? null : v.trim());
export const intOrNull = (v: string) => (v.trim() === "" ? null : Number(v));
/** API value → form input value. */
export const str = (v: string | number | null | undefined) =>
  v === null || v === undefined ? "" : String(v);

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** 5400 → "1:30" */
export function hm(seconds: number) {
  const m = Math.round(seconds / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

/** 5400 → "1:30:00" */
export function hms(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** "1.5", "1:30", "90m" → 1.5; "" → 0; nonsense → null */
export function parseHours(input: string): number | null {
  const v = input.trim().toLowerCase();
  if (v === "") return 0;
  let m = v.match(/^(\d+):(\d{1,2})$/);
  if (m) return Number(m[1]) + Number(m[2]) / 60;
  m = v.match(/^(\d+(?:\.\d+)?)\s*m$/);
  if (m) return Number(m[1]) / 60;
  m = v.match(/^(\d+(?:\.\d+)?)\s*h?$/);
  if (m) return Number(m[1]);
  return null;
}

/** Hours as a short label: "2", "1.5", "" for zero. */
export function hoursLabel(h: string | number) {
  const n = Number(h);
  if (!n) return "";
  return String(Math.round(n * 100) / 100);
}

/** Wall-clock parts of an instant in a time zone. */
export function zoned(iso: string, timeZone: string) {
  const d = new Date(iso);
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(d)
      .map((p) => [p.type, p.value]),
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
}

/** "2026-10-12" ± days */
export function addDays(day: string, n: number) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function todayIn(timeZone: string) {
  return zoned(new Date().toISOString(), timeZone).date;
}

/** "2026-10-12" → "Mon 12" (or any Intl format given). */
export function dayLabel(day: string, opts?: Intl.DateTimeFormatOptions) {
  const d = new Date(`${day}T12:00:00Z`);
  if (!opts) {
    const wd = new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" }).format(d);
    return `${wd} ${d.getUTCDate()}`;
  }
  return new Intl.DateTimeFormat(undefined, { ...opts, timeZone: "UTC" }).format(d);
}
