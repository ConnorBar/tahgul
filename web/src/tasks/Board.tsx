import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { api, del, errorMessage, patch, post, type Column, type Task } from "../api";
import { useWorkspace } from "../workspace";
import { TaskMeta, useTaskInvalidation } from "./common";

type Lanes = Record<string, string[]>; // column id → task ids, top to bottom

function lanesFrom(cols: Column[], tasks: Task[]): Lanes {
  const lanes: Lanes = Object.fromEntries(cols.map((c) => [c.id, []]));
  for (const t of [...tasks].sort((a, b) => a.position - b.position)) {
    (lanes[t.column_id] ??= []).push(t.id);
  }
  return lanes;
}

export function Board({ projectId, onOpen }: { projectId: string; onOpen: (id: string) => void }) {
  const invalidate = useTaskInvalidation();
  const cols = useQuery({
    queryKey: ["columns", projectId],
    queryFn: () => api<Column[]>(`/projects/${projectId}/columns`),
  });
  const tasks = useQuery({
    queryKey: ["tasks", "board", projectId],
    queryFn: () => api<Task[]>(`/tasks?project=${projectId}&top_level=true&sort=board`),
  });
  const byId = useMemo(() => new Map((tasks.data ?? []).map((t) => [t.id, t])), [tasks.data]);
  const [lanes, setLanes] = useState<Lanes>({});
  const [active, setActive] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cols.data && tasks.data && !active) setLanes(lanesFrom(cols.data, tasks.data));
  }, [cols.data, tasks.data, active]);

  const move = useMutation({
    mutationFn: (v: { id: string; column_id: string; index: number }) =>
      post(`/tasks/${v.id}/move`, { column_id: v.column_id, index: v.index }),
    onSuccess: () => setError(null),
    onError: (e) => setError(errorMessage(e)),
    onSettled: invalidate,
  });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const laneOf = (id: string) =>
    id in lanes ? id : Object.keys(lanes).find((k) => lanes[k].includes(id));

  const onDragStart = (e: DragStartEvent) => setActive(String(e.active.id));

  // Moving between columns updates the lanes live so the card shows where it'll land.
  const onDragOver = (e: DragOverEvent) => {
    const id = String(e.active.id);
    const overId = e.over ? String(e.over.id) : null;
    if (!overId) return;
    const from = laneOf(id);
    const to = laneOf(overId);
    if (!from || !to || from === to) return;
    setLanes((cur) => {
      const target = cur[to].filter((x) => x !== id);
      const idx = overId in cur ? target.length : Math.max(0, target.indexOf(overId));
      target.splice(idx, 0, id);
      return { ...cur, [from]: cur[from].filter((x) => x !== id), [to]: target };
    });
  };

  const onDragEnd = (e: DragEndEvent) => {
    const id = String(e.active.id);
    const overId = e.over ? String(e.over.id) : null;
    setActive(null);
    if (!overId) return;
    const lane = laneOf(id);
    if (!lane) return;
    let list = lanes[lane];
    if (!(overId in lanes) && overId !== id && list.includes(overId)) {
      const from = list.indexOf(id);
      const to = list.indexOf(overId);
      list = [...list];
      list.splice(from, 1);
      list.splice(to, 0, id);
      setLanes((cur) => ({ ...cur, [lane]: list }));
    }
    const index = list.indexOf(id);
    const original = byId.get(id);
    if (original && original.column_id === lane) {
      const before = (tasks.data ?? [])
        .filter((t) => t.column_id === lane)
        .sort((a, b) => a.position - b.position)
        .map((t) => t.id);
      if (before.indexOf(id) === index) return; // dropped where it started
    }
    move.mutate({ id, column_id: lane, index });
  };

  if (!cols.data || !tasks.data) return <p className="muted">Loading board…</p>;

  return (
    <>
      {error && <p className="error">{error}</p>}
      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onDragStart}
        onDragOver={onDragOver} onDragEnd={onDragEnd} onDragCancel={() => {
          setActive(null);
          setLanes(lanesFrom(cols.data!, tasks.data!));
        }}>
        <div className="board">
          {cols.data.map((c) => (
            <Lane key={c.id} column={c} ids={lanes[c.id] ?? []} byId={byId} onOpen={onOpen}
              projectId={projectId} allColumns={cols.data!} />
          ))}
          <AddColumn projectId={projectId} />
        </div>
        <DragOverlay>
          {active && byId.get(active) ? <Card task={byId.get(active)!} overlay /> : null}
        </DragOverlay>
      </DndContext>
    </>
  );
}

function Lane({ column, ids, byId, onOpen, projectId, allColumns }: {
  column: Column;
  ids: string[];
  byId: Map<string, Task>;
  onOpen: (id: string) => void;
  projectId: string;
  allColumns: Column[];
}) {
  const { setNodeRef, isOver } = useDroppable({ id: column.id });
  return (
    <section className={`lane${isOver ? " over" : ""}`} aria-label={column.name}>
      <LaneHeader column={column} count={ids.length} allColumns={allColumns} />
      <SortableContext id={column.id} items={ids} strategy={verticalListSortingStrategy}>
        <div ref={setNodeRef} className="lane-cards">
          {ids.map((id) => byId.get(id) && (
            <SortableCard key={id} task={byId.get(id)!} onOpen={onOpen} />
          ))}
          {ids.length === 0 && <div className="lane-empty muted small">Drop tasks here</div>}
        </div>
      </SortableContext>
      <AddCard projectId={projectId} column={column} />
    </section>
  );
}

function LaneHeader({ column, count, allColumns }: { column: Column; count: number; allColumns: Column[] }) {
  const invalidate = useTaskInvalidation();
  const [menu, setMenu] = useState(false);
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch(`/columns/${column.id}`, body),
    onSettled: invalidate,
  });
  const remove = useMutation({
    mutationFn: (moveTo: string | null) =>
      del(`/columns/${column.id}${moveTo ? `?move_to=${moveTo}` : ""}`),
    onSettled: invalidate,
  });
  const others = allColumns.filter((c) => c.id !== column.id);
  return (
    <header className="lane-head">
      <h3>
        {column.name} <span className="muted">{count}</span>
        {column.is_done && <span className="tag">done</span>}
      </h3>
      <button className="icon" aria-label={`${column.name} column options`} aria-expanded={menu}
        onClick={() => setMenu(!menu)}>⋯</button>
      {menu && (
        <div className="menu" role="menu" onMouseLeave={() => setMenu(false)}>
          <button role="menuitem" onClick={() => {
            const name = prompt("Rename column", column.name);
            if (name && name.trim() !== column.name) update.mutate({ name: name.trim() });
            setMenu(false);
          }}>Rename</button>
          <button role="menuitem" onClick={() => { update.mutate({ is_done: !column.is_done }); setMenu(false); }}>
            {column.is_done ? "Don't count as done" : "Count as done"}
          </button>
          {column.position > 0 && (
            <button role="menuitem" onClick={() => { update.mutate({ position: column.position - 1 }); setMenu(false); }}>
              Move left
            </button>
          )}
          {column.position < allColumns.length - 1 && (
            <button role="menuitem" onClick={() => { update.mutate({ position: column.position + 1 }); setMenu(false); }}>
              Move right
            </button>
          )}
          {others.length > 0 && (
            <button role="menuitem" className="danger-text" onClick={() => {
              setMenu(false);
              if (count === 0) {
                if (confirm(`Delete the ${column.name} column?`)) remove.mutate(null);
                return;
              }
              const target = others.find((c) => !c.is_done) ?? others[0];
              if (confirm(`Delete ${column.name}? Its ${count} task(s) move to ${target.name}.`))
                remove.mutate(target.id);
            }}>Delete column</button>
          )}
        </div>
      )}
      {(update.isError || remove.isError) && <p className="error small">{errorMessage(update.error ?? remove.error)}</p>}
    </header>
  );
}

function SortableCard({ task, onOpen }: { task: Task; onOpen: (id: string) => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: task.id });
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
      className={isDragging ? "dragging" : undefined} {...attributes} {...listeners}
      onClick={() => onOpen(task.id)}
      onKeyDown={(e) => {
        listeners?.onKeyDown?.(e);
        if (e.key === "Enter" && !e.defaultPrevented) onOpen(task.id);
      }}
      aria-roledescription="draggable task" aria-label={`${task.ref} ${task.title}`}>
      <Card task={task} />
    </div>
  );
}

function Card({ task, overlay }: { task: Task; overlay?: boolean }) {
  const { current: ws } = useWorkspace();
  return (
    <article className={`card-task${overlay ? " overlay" : ""}${task.done ? " is-done" : ""}`}>
      <p className="card-title">{task.title}</p>
      <TaskMeta task={task} timeZone={ws.timezone} />
    </article>
  );
}

function AddCard({ projectId, column }: { projectId: string; column: Column }) {
  const invalidate = useTaskInvalidation();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const add = useMutation({
    mutationFn: () => post("/tasks", { project_id: projectId, title, status: column.id }),
    onSuccess: () => { setTitle(""); invalidate(); },
  });
  if (!open)
    return <button className="add-card ghost" onClick={() => setOpen(true)}>+ Add task</button>;
  return (
    <form className="add-card-form" onSubmit={(e) => { e.preventDefault(); if (title.trim()) add.mutate(); }}>
      <textarea rows={2} value={title} autoFocus aria-label={`New task in ${column.name}`}
        onChange={(e) => setTitle(e.target.value)} placeholder="Task title"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (title.trim()) add.mutate(); }
          if (e.key === "Escape") setOpen(false);
        }} />
      <div className="row">
        <button disabled={!title.trim() || add.isPending}>Add</button>
        <button type="button" className="ghost" onClick={() => setOpen(false)}>Done</button>
      </div>
    </form>
  );
}

function AddColumn({ projectId }: { projectId: string }) {
  const invalidate = useTaskInvalidation();
  const add = useMutation({
    mutationFn: (name: string) => post(`/projects/${projectId}/columns`, { name }),
    onSettled: invalidate,
  });
  return (
    <div className="lane add-lane">
      <button className="ghost" onClick={() => {
        const name = prompt("New column name");
        if (name?.trim()) add.mutate(name.trim());
      }}>+ Add column</button>
      {add.isError && <p className="error small">{errorMessage(add.error)}</p>}
    </div>
  );
}
