import { addDays, hm } from "../ui";

/**
 * Start and end, each with its own date. Work that runs past midnight is entered as it
 * happened: start Oct 21 22:00, end Oct 22 01:30. Changing the start moves the end with
 * it (same length); choosing an end time earlier than the start on the same day offers
 * the next day, but the end date is always yours to set.
 */
export type DTRange = { start: string; end: string }; // "YYYY-MM-DDTHH:MM", local

const split = (v: string) => ({ date: v.slice(0, 10), time: v.slice(11, 16) || "00:00" });
const join = (date: string, time: string) => `${date}T${time}`;

export function minutesBetween(a: string, b: string): number {
  const da = Date.parse(`${a.slice(0, 10)}T00:00:00Z`) / 60000;
  const db = Date.parse(`${b.slice(0, 10)}T00:00:00Z`) / 60000;
  const ma = Number(a.slice(11, 13)) * 60 + Number(a.slice(14, 16));
  const mb = Number(b.slice(11, 13)) * 60 + Number(b.slice(14, 16));
  return db + mb - (da + ma);
}

function addMinutes(v: string, minutes: number): string {
  const { date, time } = split(v);
  let total = Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5)) + minutes;
  let d = date;
  while (total < 0) { total += 1440; d = addDays(d, -1); }
  while (total >= 1440) { total -= 1440; d = addDays(d, 1); }
  return join(d, `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`);
}

export function DateTimeRange({
  value,
  onChange,
  allDay = false,
  idPrefix = "dtr",
}: {
  value: DTRange;
  onChange: (v: DTRange) => void;
  allDay?: boolean;
  idPrefix?: string;
}) {
  const s = split(value.start);
  const e = split(value.end);
  const length = minutesBetween(value.start, value.end);

  const setStart = (date: string, time: string) => {
    const start = join(date, time);
    onChange({ start, end: addMinutes(start, Math.max(length, allDay ? 0 : 15)) });
  };
  const setEndTime = (time: string) => {
    let date = e.date;
    // Same day and earlier than the start: they almost certainly mean the next day.
    if (date === s.date && time <= s.time) date = addDays(s.date, 1);
    onChange({ start: value.start, end: join(date, time) });
  };

  const bad = allDay ? e.date < s.date : length <= 0;
  const crosses = !allDay && e.date !== s.date;

  return (
    <div className="dtr">
      <div className="dtr-row">
        <label htmlFor={`${idPrefix}-sd`}>Starts</label>
        <input id={`${idPrefix}-sd`} type="date" value={s.date} required onChange={(ev) => ev.target.value && setStart(ev.target.value, s.time)} />
        {!allDay && (
          <input aria-label="Start time" type="time" value={s.time} required step={300}
            onChange={(ev) => ev.target.value && setStart(s.date, ev.target.value)} />
        )}
      </div>
      <div className="dtr-row">
        <label htmlFor={`${idPrefix}-ed`}>Ends</label>
        <input id={`${idPrefix}-ed`} type="date" value={e.date} min={s.date} required
          onChange={(ev) => ev.target.value && onChange({ start: value.start, end: join(ev.target.value, e.time) })} />
        {!allDay && (
          <input aria-label="End time" type="time" value={e.time} required step={300}
            onChange={(ev) => ev.target.value && setEndTime(ev.target.value)} />
        )}
      </div>
      <p className={`dtr-note small ${bad ? "error" : "muted"}`}>
        {bad
          ? "The end has to be after the start."
          : allDay
            ? `${minutesBetween(join(s.date, "00:00"), join(e.date, "00:00")) / 1440 + 1} day(s)`
            : `${hm(length * 60)}${crosses ? " · runs past midnight" : ""}`}
      </p>
    </div>
  );
}
