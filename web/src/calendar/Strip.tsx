import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, forwardRef } from "react";
import { addDays, todayIn, zoned } from "../ui";

/**
 * A continuous, horizontally scrolling 24-hour calendar (like iCal's week view):
 * days are columns you scroll or swipe through, hours run top to bottom, and the
 * time gutter and day headers stay put. Everything works in the workspace's local
 * time as "YYYY-MM-DDTHH:MM" strings; the API reads those in the workspace time zone.
 */

export type StripItem = {
  key: string;
  start: string; // ISO instant
  end: string;
  allDay: boolean;
  title: string;
  sub?: string;
  colour?: string | null;
  className?: string;
  editable: boolean;
  data: unknown;
};

export type LocalRange = { start: string; end: string; allDay: boolean }; // naive local

export type StripHandle = {
  today: () => void;
  page: (dir: 1 | -1) => void;
  goTo: (day: string) => void;
};

type Props = {
  items: StripItem[];
  timeZone: string;
  visibleDays: number;
  weekStartsMonday?: boolean;
  onRange: (from: string, to: string) => void;
  onVisible?: (firstDay: string) => void;
  onSelect: (r: LocalRange) => void;
  onOpen: (item: StripItem) => void;
  onMove: (item: StripItem, start: string, end: string) => void;
};

const SNAP = 15; // minutes
const GUTTER = 56;
const HEADER = 46;
const CHUNK = 28; // days added when you scroll near an edge

// --- local time helpers -------------------------------------------------------------------

type Local = { date: string; min: number }; // minutes after local midnight

function toLocal(iso: string, tz: string): Local {
  const z = zoned(iso, tz);
  const [h, m] = z.time.split(":").map(Number);
  return { date: z.date, min: h * 60 + m };
}

function fmtLocal(l: Local): string {
  // Normalise minutes outside 0..1439 by moving across days.
  let { date, min } = l;
  while (min < 0) { min += 1440; date = addDays(date, -1); }
  while (min >= 1440) { min -= 1440; date = addDays(date, 1); }
  const h = String(Math.floor(min / 60)).padStart(2, "0");
  const m = String(min % 60).padStart(2, "0");
  return `${date}T${h}:${m}`;
}

function shift(l: Local, minutes: number): Local {
  return { date: l.date, min: l.min + minutes };
}

function dayDiff(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / 86400000);
}

const snap = (min: number) => Math.round(min / SNAP) * SNAP;

function hourLabel(h: number) {
  if (h === 0) return "12 AM";
  if (h === 12) return "Noon";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

function weekday(day: string) {
  return new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" }).format(new Date(`${day}T12:00:00Z`));
}

// --- layout -------------------------------------------------------------------------------

type Seg = {
  item: StripItem;
  day: number; // column index
  top: number; // minutes
  bottom: number;
  first: boolean;
  last: boolean;
  lane: number;
  lanes: number;
};

function layoutTimed(items: StripItem[], days: string[], tz: string): Seg[] {
  const index = new Map(days.map((d, i) => [d, i]));
  const byDay: Seg[][] = days.map(() => []);
  for (const item of items) {
    if (item.allDay) continue;
    const s = toLocal(item.start, tz);
    let e = toLocal(item.end, tz);
    if (dayDiff(s.date, e.date) * 1440 + e.min - s.min < SNAP) {
      e = { date: s.date, min: s.min + SNAP }; // keep tiny things clickable
    }
    const span = dayDiff(s.date, e.date);
    for (let k = 0; k <= span; k++) {
      const date = k === 0 ? s.date : addDays(s.date, k);
      const top = k === 0 ? s.min : 0;
      const bottom = k === span ? e.min : 1440;
      if (bottom <= top) continue; // ends exactly at midnight
      const col = index.get(date);
      if (col === undefined) continue;
      byDay[col].push({ item, day: col, top, bottom, first: k === 0, last: k === span || (k === span - 1 && e.min === 0), lane: 0, lanes: 1 });
    }
  }
  // Side-by-side lanes for overlapping items in each day.
  for (const segs of byDay) {
    segs.sort((a, b) => a.top - b.top || b.bottom - a.bottom);
    let cluster: Seg[] = [];
    let clusterEnd = -1;
    const flush = () => {
      const lanes: number[] = [];
      for (const s of cluster) {
        let lane = lanes.findIndex((end) => end <= s.top);
        if (lane === -1) { lane = lanes.length; lanes.push(s.bottom); } else lanes[lane] = s.bottom;
        s.lane = lane;
      }
      for (const s of cluster) s.lanes = lanes.length;
    };
    for (const s of segs) {
      if (s.top >= clusterEnd && cluster.length) { flush(); cluster = []; }
      cluster.push(s);
      clusterEnd = Math.max(clusterEnd, s.bottom);
    }
    if (cluster.length) flush();
  }
  return byDay.flat();
}

type AllDaySeg = { item: StripItem; from: number; to: number; row: number };

function layoutAllDay(items: StripItem[], days: string[], tz: string): { segs: AllDaySeg[]; rows: number } {
  const first = days[0];
  const segs: AllDaySeg[] = [];
  const rows: number[][] = [];
  for (const item of items) {
    if (!item.allDay) continue;
    // All-day events are stored as UTC midnights; their dates are what matter.
    const s = item.start.slice(0, 10);
    const eRaw = item.end.slice(0, 10);
    const e = eRaw > s ? addDays(eRaw, -1) : s;
    const from = Math.max(0, dayDiff(first, s));
    const to = Math.min(days.length - 1, dayDiff(first, e));
    if (to < 0 || from > days.length - 1) continue;
    let row = rows.findIndex((r) => r.every((x) => x < from || x > to));
    if (row === -1) { row = rows.length; rows.push([]); }
    for (let i = from; i <= to; i++) rows[row].push(i);
    segs.push({ item, from, to, row });
  }
  void tz;
  return { segs, rows: rows.length };
}

// --- component ----------------------------------------------------------------------------

type Drag =
  | { kind: "select"; day: number; from: number; to: number; pointer: number }
  | { kind: "move" | "resize"; item: StripItem; x0: number; y0: number; dMin: number; dDay: number; moved: boolean; pointer: number };

export const Strip = forwardRef<StripHandle, Props>(function Strip(props, ref) {
  const { items, timeZone, visibleDays, onRange, onVisible, onSelect, onOpen, onMove } = props;
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const today = todayIn(timeZone);
  const [days, setDays] = useState<string[]>(() => Array.from({ length: 70 }, (_, i) => addDays(today, i - 28)));
  const pendingShift = useRef(0);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [now, setNow] = useState(() => toLocal(new Date().toISOString(), timeZone));
  const hourH = width && width < 640 ? 44 : 48;
  const colW = width ? Math.max(visibleDays === 1 ? 120 : 64, (width - GUTTER) / visibleDays) : 120;
  const pxPerMin = hourH / 60;

  useEffect(() => {
    const t = setInterval(() => setNow(toLocal(new Date().toISOString(), timeZone)), 60_000);
    return () => clearInterval(t);
  }, [timeZone]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => { onRange(days[0], days[days.length - 1]); }, [days, onRange]);

  const scrollToIndex = useCallback((i: number, smooth = false) => {
    box.current?.scrollTo({ left: Math.max(0, i * colW), behavior: smooth ? "smooth" : "auto" });
  }, [colW]);

  const anchorIndex = useCallback((day: string) => {
    let target = day;
    if (visibleDays === 7) {
      const dow = new Date(`${day}T12:00:00Z`).getUTCDay();
      target = addDays(day, -((dow + 6) % 7)); // Monday
    }
    return dayDiff(days[0], target);
  }, [days, visibleDays]);

  // First paint, and whenever the zoom changes: today in view, about 7am at the top.
  const placed = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (!width || placed.current === visibleDays) return;
    placed.current = visibleDays;
    scrollToIndex(anchorIndex(today));
    const el = box.current!;
    const start = Math.min(now.min - 90, 7 * 60);
    el.scrollTop = Math.max(0, start * pxPerMin);
  }, [width, visibleDays, anchorIndex, scrollToIndex, today, now.min, pxPerMin]);

  // Keep the view still when days are added on the left.
  useLayoutEffect(() => {
    if (pendingShift.current && box.current) {
      box.current.scrollLeft += pendingShift.current * colW;
      pendingShift.current = 0;
    }
  }, [days, colW]);

  useImperativeHandle(ref, () => ({
    today: () => scrollToIndex(anchorIndex(today), true),
    page: (dir) => {
      const el = box.current!;
      const first = Math.round(el.scrollLeft / colW);
      scrollToIndex(first + dir * visibleDays, true);
    },
    goTo: (day) => {
      if (day < days[0] || day > days[days.length - 1]) {
        setDays(Array.from({ length: 70 }, (_, i) => addDays(day, i - 28)));
        placed.current = null;
        return;
      }
      scrollToIndex(anchorIndex(day), true);
    },
  }), [scrollToIndex, anchorIndex, today, colW, visibleDays, days]);

  const onScroll = () => {
    const el = box.current!;
    const first = Math.floor((el.scrollLeft + 1) / colW);
    onVisible?.(days[Math.min(days.length - 1, Math.max(0, first))]);
    if (el.scrollLeft < colW * 3) {
      pendingShift.current = CHUNK;
      setDays((d) => [...Array.from({ length: CHUNK }, (_, i) => addDays(d[0], i - CHUNK)), ...d]);
    } else if (el.scrollLeft + el.clientWidth > el.scrollWidth - colW * 3) {
      setDays((d) => [...d, ...Array.from({ length: CHUNK }, (_, i) => addDays(d[d.length - 1], i + 1))]);
    }
  };

  const timed = useMemo(() => layoutTimed(items, days, timeZone), [items, days, timeZone]);
  const allDay = useMemo(() => layoutAllDay(items, days, timeZone), [items, days, timeZone]);
  const allDayH = allDay.rows ? allDay.rows * 24 + 8 : 0;
  const bodyTop = HEADER + allDayH;

  // --- pointer handling -----------------------------------------------------------------

  const minuteAt = (clientY: number) => {
    const el = box.current!;
    const y = clientY - el.getBoundingClientRect().top + el.scrollTop - bodyTop;
    return Math.max(0, Math.min(1440, y / pxPerMin));
  };

  const startSelect = (e: React.PointerEvent, day: number) => {
    if (e.button !== 0) return;
    const m = Math.floor(minuteAt(e.clientY) / SNAP) * SNAP;
    if (e.pointerType !== "mouse") {
      // Touch: a tap makes an hour; dragging scrolls as usual.
      const startY = e.clientY;
      const up = (ev: PointerEvent) => {
        window.removeEventListener("pointerup", up);
        if (Math.abs(ev.clientY - startY) < 8) {
          const s = { date: days[day], min: m };
          onSelect({ start: fmtLocal(s), end: fmtLocal(shift(s, 60)), allDay: false });
        }
      };
      window.addEventListener("pointerup", up);
      return;
    }
    e.preventDefault();
    setDrag({ kind: "select", day, from: m, to: m + SNAP, pointer: e.pointerId });
  };

  const startItem = (e: React.PointerEvent, item: StripItem, kind: "move" | "resize") => {
    e.stopPropagation();
    if (e.button !== 0) return;
    if (e.pointerType !== "mouse" || !item.editable) {
      const x0 = e.clientX, y0 = e.clientY;
      const up = (ev: PointerEvent) => {
        window.removeEventListener("pointerup", up);
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 8) onOpen(item);
      };
      window.addEventListener("pointerup", up);
      return;
    }
    e.preventDefault();
    setDrag({ kind, item, x0: e.clientX, y0: e.clientY, dMin: 0, dDay: 0, moved: false, pointer: e.pointerId });
  };

  useEffect(() => {
    if (!drag) return;
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== drag.pointer) return;
      if (drag.kind === "select") {
        const m = Math.ceil(minuteAt(ev.clientY) / SNAP) * SNAP;
        setDrag({ ...drag, to: Math.max(m, drag.from + SNAP) });
      } else {
        const dx = ev.clientX - drag.x0, dy = ev.clientY - drag.y0;
        const moved = drag.moved || Math.hypot(dx, dy) > 4;
        setDrag({ ...drag, moved, dMin: snap(dy / pxPerMin), dDay: drag.kind === "move" ? Math.round(dx / colW) : 0 });
      }
    };
    const up = (ev: PointerEvent) => {
      if (ev.pointerId !== drag.pointer) return;
      if (drag.kind === "select") {
        const s = { date: days[drag.day], min: drag.from };
        const len = drag.to - drag.from <= SNAP ? 60 : drag.to - drag.from;
        onSelect({ start: fmtLocal(s), end: fmtLocal(shift(s, len)), allDay: false });
      } else if (!drag.moved) {
        onOpen(drag.item);
      } else {
        const s = toLocal(drag.item.start, timeZone);
        const e = toLocal(drag.item.end, timeZone);
        if (drag.kind === "move") {
          const ns = { date: addDays(s.date, drag.dDay), min: s.min + drag.dMin };
          const ne = { date: addDays(e.date, drag.dDay), min: e.min + drag.dMin };
          if (drag.dMin || drag.dDay) onMove(drag.item, fmtLocal(ns), fmtLocal(ne));
        } else {
          const startAbs = 0, endAbs = dayDiff(s.date, e.date) * 1440 + e.min - s.min + drag.dMin;
          if (drag.dMin && endAbs > startAbs + SNAP - 1) onMove(drag.item, fmtLocal(s), fmtLocal(shift(s, endAbs)));
        }
      }
      setDrag(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const totalW = GUTTER + days.length * colW;
  const dragKey = drag && drag.kind !== "select" ? drag.item.key : null;

  return (
    <div className="strip" ref={box} onScroll={onScroll}
      style={{ ["--col" as string]: `${colW}px`, ["--hour" as string]: `${hourH}px`, scrollPaddingLeft: GUTTER }}>
      <div className="strip-inner" style={{ width: totalW }}>
        {/* Day headers and the all-day row stay at the top. */}
        <div className="strip-head" style={{ height: HEADER + allDayH }}>
          <div className="strip-corner" style={{ width: GUTTER, height: HEADER + allDayH }}>
            {allDayH > 0 && <span className="strip-allday-label" style={{ top: HEADER + 4 }}>all-day</span>}
          </div>
          <div className="strip-head-days" style={{ width: days.length * colW }}>
            {days.map((d, i) => {
              const dom = Number(d.slice(8));
              return (
                <div key={d} className={`strip-day-head${d === today ? " today" : ""}${dom === 1 ? " month-start" : ""}`}
                  style={{ left: i * colW, width: colW }}>
                  <span className="wd">{weekday(d)}</span>
                  <span className="dom">{dom}</span>
                  {dom === 1 && <span className="mon">{new Intl.DateTimeFormat(undefined, { month: "short", timeZone: "UTC" }).format(new Date(`${d}T12:00:00Z`))}</span>}
                </div>
              );
            })}
            {allDay.segs.map((s) => (
              <button key={s.item.key} type="button" className={`strip-allday ${s.item.className ?? ""}`}
                style={{ left: s.from * colW + 2, width: (s.to - s.from + 1) * colW - 4, top: HEADER + 4 + s.row * 24,
                  ["--c" as string]: s.item.colour ?? "var(--muted)" }}
                onClick={() => onOpen(s.item)} title={s.item.title}>
                {s.item.title}
              </button>
            ))}
          </div>
        </div>

        <div className="strip-row">
          {/* Hour labels stay on the left. */}
          <div className="strip-gutter" style={{ width: GUTTER, height: 24 * hourH }}>
            {Array.from({ length: 24 }, (_, h) => (
              <span key={h} style={{ top: h * hourH }}>{h ? hourLabel(h) : ""}</span>
            ))}
          </div>

          <div className="strip-body" style={{ width: days.length * colW, height: 24 * hourH }}>
            {days.map((d, i) => (
              <div key={d} className={`strip-col${d === today ? " today" : ""}${new Date(`${d}T12:00:00Z`).getUTCDay() % 6 === 0 ? " weekend" : ""}`}
                style={{ left: i * colW, width: colW }} onPointerDown={(e) => startSelect(e, i)} />
            ))}
            {days.includes(today) && (
              <div className="strip-now" style={{ left: dayDiff(days[0], today) * colW, width: colW, top: now.min * pxPerMin }} />
            )}
            {drag?.kind === "select" && (
              <div className="strip-selecting" style={{ left: drag.day * colW + 2, width: colW - 4, top: drag.from * pxPerMin, height: (drag.to - drag.from) * pxPerMin }}>
                {fmtLocal({ date: days[drag.day], min: drag.from }).slice(11)}–{fmtLocal({ date: days[drag.day], min: drag.to }).slice(11)}
              </div>
            )}
            {timed.map((s) => {
              const dragging = dragKey === s.item.key && drag && drag.kind !== "select" && drag.moved ? drag : null;
              const dDay = dragging?.kind === "move" ? dragging.dDay : 0;
              const dTop = dragging?.kind === "move" ? dragging.dMin : 0;
              const dBottom = dragging ? dragging.dMin : 0;
              const top = s.top + dTop;
              const bottom = s.bottom + (dragging?.kind === "resize" && s.last ? dBottom : dTop);
              const w = (colW - 6) / s.lanes;
              const h = Math.max(18, (bottom - top) * pxPerMin - 2);
              const short = h < 36;
              return (
                <div key={`${s.item.key}:${s.day}`}
                  className={`strip-item ${s.item.className ?? ""}${dragging ? " dragging" : ""}${short ? " short" : ""}${s.first ? "" : " cont"}${s.last ? "" : " goes-on"}`}
                  style={{ left: (s.day + dDay) * colW + 3 + s.lane * w, width: w - 2, top: top * pxPerMin + 1, height: h,
                    ["--c" as string]: s.item.colour ?? "var(--accent)" }}
                  onPointerDown={(e) => startItem(e, s.item, "move")}
                  role="button" tabIndex={0} aria-label={`${s.item.title} ${s.item.sub ?? ""}`}
                  onKeyDown={(e) => e.key === "Enter" && onOpen(s.item)}>
                  <span className="t">{s.item.title}</span>
                  {!short && s.item.sub && <span className="s">{s.item.sub}</span>}
                  {s.last && s.item.editable && (
                    <span className="strip-resize" onPointerDown={(e) => startItem(e, s.item, "resize")} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
});

export { fmtLocal, toLocal };
