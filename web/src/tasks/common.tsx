import { useQueryClient } from "@tanstack/react-query";
import type { Priority, Task } from "../api";
import { dayLabel, hm, todayIn } from "../ui";

export const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: "Urgent",
  high: "High",
  medium: "Medium",
  low: "Low",
  none: "No priority",
};

export function PriorityBadge({ p }: { p: Priority }) {
  if (p === "none") return null;
  return <span className={`prio prio-${p}`} title={`${PRIORITY_LABEL[p]} priority`}>{PRIORITY_LABEL[p]}</span>;
}

export function DueLabel({ task, timeZone }: { task: Task; timeZone: string }) {
  if (!task.due_date) return null;
  const today = todayIn(timeZone);
  const tomorrow = new Date(`${today}T12:00:00Z`);
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  const label =
    task.due_date === today
      ? "Today"
      : task.due_date === tomorrow.toISOString().slice(0, 10)
        ? "Tomorrow"
        : dayLabel(task.due_date, { month: "short", day: "numeric" });
  const time = task.due_time ? ` ${task.due_time.slice(0, 5)}` : "";
  const cls = task.done ? "due" : task.overdue ? "due overdue" : task.due_date === today ? "due soon" : "due";
  return <span className={cls}>{label}{time}</span>;
}

/** The compact bits under a card's title. */
export function TaskMeta({ task, timeZone }: { task: Task; timeZone: string }) {
  return (
    <span className="task-meta">
      <span className="muted ref">{task.ref}</span>
      <PriorityBadge p={task.priority} />
      {task.type !== "task" && <span className={`ttype ttype-${task.type}`}>{task.type}</span>}
      <DueLabel task={task} timeZone={timeZone} />
      {task.blocked && <span className="blocked" title="Waiting on another task">Blocked</span>}
      {task.subtask_count > 0 && (
        <span className="muted" title="Subtasks done">☑ {task.subtasks_done}/{task.subtask_count}</span>
      )}
      {task.comment_count > 0 && <span className="muted" title="Comments">💬 {task.comment_count}</span>}
      {task.tracked_seconds > 0 && <span className="muted" title="Time logged">⏱ {hm(task.tracked_seconds)}</span>}
      {task.labels.map((l) => <span key={l} className="label">#{l}</span>)}
    </span>
  );
}

export function useTaskInvalidation() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      ["tasks", "task", "columns", "history", "timer", "entries"].map((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      ),
    );
}
