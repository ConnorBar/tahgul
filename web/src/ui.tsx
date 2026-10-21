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
