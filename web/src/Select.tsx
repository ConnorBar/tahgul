import { createPortal } from "react-dom";
import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactElement,
  type ReactNode,
} from "react";

/**
 * A drop-in for <select> that matches the rest of the UI.
 *
 * Use it exactly like a native select (`value`, `onChange={(e) => …e.target.value}`,
 * <option> and <optgroup> children). With a mouse it opens Tahgul's own list; on touch
 * screens it stays a real <select> (styled to match), so phones keep their native
 * picker wheel.
 */

type Opt = { value: string; label: string; disabled?: boolean; group?: string };

type Props = {
  value: string;
  onChange: (e: ChangeEvent<HTMLSelectElement>) => void;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  required?: boolean;
  id?: string;
  "aria-label"?: string;
  title?: string;
};

function text(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement(node)) return text((node.props as { children?: ReactNode }).children);
  return "";
}

function collect(children: ReactNode, group?: string, out: Opt[] = []): Opt[] {
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    const el = child as ReactElement<{ value?: string | number; children?: ReactNode; disabled?: boolean; label?: string }>;
    if (el.type === "option") {
      const label = text(el.props.children);
      out.push({ value: el.props.value !== undefined ? String(el.props.value) : label, label, disabled: el.props.disabled, group });
    } else if (el.type === "optgroup") {
      collect(el.props.children, el.props.label, out);
    } else if (el.props.children) {
      collect(el.props.children, group, out);
    }
  });
  return out;
}

const coarse = () => typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches;

export function Select(props: Props) {
  const { value, onChange, children, className, disabled, id, title } = props;
  const [touch] = useState(coarse);
  if (touch) {
    return (
      <select className={`ui-select native ${className ?? ""}`} value={value} onChange={onChange} disabled={disabled}
        required={props.required} id={id} aria-label={props["aria-label"]} title={title}>
        {children}
      </select>
    );
  }
  return <Listbox {...props} />;
}

function Listbox({ value, onChange, children, className, disabled, id, title, ...rest }: Props) {
  const opts = useMemo(() => collect(children), [children]);
  const listId = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<{ left: number; top: number; width: number; up: boolean; maxH: number } | null>(null);
  const typed = useRef({ s: "", t: 0 });
  const current = opts.find((o) => o.value === value) ?? opts[0];

  const place = () => {
    const r = btn.current!.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 8;
    const above = r.top - 8;
    const want = Math.min(320, opts.length * 36 + 12);
    const up = below < Math.min(want, 200) && above > below;
    setPos({ left: r.left, top: up ? r.top - 4 : r.bottom + 4, width: Math.max(r.width, 180), up, maxH: Math.min(want, up ? above : below) });
  };

  const show = () => {
    if (disabled) return;
    place();
    setActive(Math.max(0, opts.findIndex((o) => o.value === value)));
    setOpen(true);
  };
  const close = (focus = true) => {
    setOpen(false);
    if (focus) btn.current?.focus();
  };
  const pick = (o: Opt) => {
    if (o.disabled) return;
    close();
    if (o.value !== value) {
      onChange({ target: { value: o.value }, currentTarget: { value: o.value } } as unknown as ChangeEvent<HTMLSelectElement>);
    }
  };

  useLayoutEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      if (!btn.current?.contains(e.target as Node) && !list.current?.contains(e.target as Node)) close(false);
    };
    const reflow = (e: Event) => {
      if (list.current?.contains(e.target as Node)) return;
      place();
    };
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("scroll", reflow, true);
    window.addEventListener("resize", reflow);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("scroll", reflow, true);
      window.removeEventListener("resize", reflow);
    };
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const step = (from: number, dir: 1 | -1) => {
    let i = from;
    for (let n = 0; n < opts.length; n++) {
      i = (i + dir + opts.length) % opts.length;
      if (!opts[i].disabled) return i;
    }
    return from;
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); show(); }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => step(a, 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => step(a, -1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(step(-1, 1)); }
    else if (e.key === "End") { e.preventDefault(); setActive(step(opts.length, -1)); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (opts[active]) pick(opts[active]); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") { close(false); }
    else if (e.key.length === 1) {
      const now = Date.now();
      typed.current = { s: (now - typed.current.t < 700 ? typed.current.s : "") + e.key.toLowerCase(), t: now };
      const i = opts.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(typed.current.s));
      if (i >= 0) setActive(i);
    }
  };

  let lastGroup: string | undefined;
  return (
    <>
      <button ref={btn} type="button" id={id} className={`ui-select ${className ?? ""}${open ? " open" : ""}`}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
        aria-label={rest["aria-label"]} title={title ?? current?.label} disabled={disabled}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        onClick={() => (open ? close() : show())} onKeyDown={onKey}>
        <span className="ui-select-value">{current?.label ?? ""}</span>
        <svg className="ui-select-chevron" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && pos && createPortal(
        <ul ref={list} id={listId} role="listbox" className="ui-select-list" tabIndex={-1}
          aria-label={rest["aria-label"]}
          style={{ left: pos.left, width: pos.width, maxHeight: pos.maxH,
            ...(pos.up ? { bottom: window.innerHeight - pos.top } : { top: pos.top }) }}>
          {opts.map((o, i) => {
            const header = o.group && o.group !== lastGroup ? o.group : null;
            lastGroup = o.group;
            return (
              <li key={`${o.value}-${i}`} role="presentation">
                {header && <div className="ui-select-group" role="presentation">{header}</div>}
                <div role="option" id={`${listId}-${i}`} data-i={i} aria-selected={o.value === value}
                  aria-disabled={o.disabled || undefined}
                  className={`ui-select-opt${i === active ? " active" : ""}${o.value === value ? " selected" : ""}${o.group ? " in-group" : ""}`}
                  onPointerMove={() => !o.disabled && setActive(i)}
                  onClick={() => pick(o)}>
                  {o.label}
                  {o.value === value && (
                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                      <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
              </li>
            );
          })}
        </ul>,
        // Inside an open dialog the list must live in the dialog (top layer); elsewhere, the page.
        btn.current?.closest("dialog") ?? document.body,
      )}
    </>
  );
}
