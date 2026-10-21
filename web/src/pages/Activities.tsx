import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, errorMessage, patch, post, type Activity } from "../api";
import { Dialog, Empty, money, orNull, str } from "../ui";
import { useWorkspace } from "../workspace";

export default function Activities() {
  const { current: ws } = useWorkspace();
  const activities = useQuery({
    queryKey: ["activities", ws.id],
    queryFn: () => api<Activity[]>(`/activities?workspace_id=${ws.id}`),
  });
  const [editing, setEditing] = useState<Activity | "new" | null>(null);

  return (
    <div className="card">
      <div className="page-head">
        <h2>Activities</h2>
        <button className="ghost" onClick={() => setEditing("new")}>New activity</button>
      </div>
      <p className="muted">
        The kinds of work you log: audit, setup, support, travel. An activity rate beats the
        project and client rate, e.g. a lower rate for travel.
      </p>
      {activities.data?.length === 0 && <Empty>No activities yet.</Empty>}
      {activities.data && activities.data.length > 0 && (
        <div className="table-wrap">
          <table className="clickable">
            <thead>
              <tr>
                <th>Name</th>
                <th className="num">Rate</th>
                <th>Billable</th>
              </tr>
            </thead>
            <tbody>
              {activities.data.map((a) => (
                <tr key={a.id} onClick={() => setEditing(a)} tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && setEditing(a)}>
                  <td><strong>{a.name}</strong></td>
                  <td className="num">
                    {a.rate ? money(a.rate, ws.currency, true) : <span className="muted">—</span>}
                  </td>
                  <td>{a.billable ? "Yes" : <span className="muted">No</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Dialog open={editing !== null} title={editing === "new" ? "New activity" : "Edit activity"}
        onClose={() => setEditing(null)}>
        {editing !== null && (
          <ActivityForm activity={editing === "new" ? null : editing}
            onDone={() => setEditing(null)} />
        )}
      </Dialog>
    </div>
  );
}

function ActivityForm({ activity, onDone }: { activity: Activity | null; onDone: () => void }) {
  const qc = useQueryClient();
  const { current: ws } = useWorkspace();
  const [name, setName] = useState(activity?.name ?? "");
  const [rate, setRate] = useState(str(activity?.rate));
  const [billable, setBillable] = useState(activity?.billable ?? true);

  const done = () => {
    qc.invalidateQueries({ queryKey: ["activities"] });
    qc.invalidateQueries({ queryKey: ["rate"] });
    onDone();
  };
  const body = { name, rate: orNull(rate), billable };
  const save = useMutation({
    mutationFn: () =>
      activity
        ? patch(`/activities/${activity.id}`, body)
        : post("/activities", { ...body, workspace_id: ws.id }),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: () => del(`/activities/${activity!.id}`),
    onSuccess: done,
  });

  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <label>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100}
          autoFocus />
      </label>
      <label>
        Rate override ({ws.currency}/h)
        <input inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)}
          placeholder="Use the project's rate" />
      </label>
      <label className="check">
        <input type="checkbox" checked={billable} onChange={(e) => setBillable(e.target.checked)} />
        <span>Billable by default</span>
      </label>
      {(save.isError || remove.isError) && (
        <p className="error">{errorMessage(save.error ?? remove.error)}</p>
      )}
      <div className="dialog-actions">
        {activity && (
          <button type="button" className="danger" disabled={remove.isPending}
            onClick={() => confirm(`Delete ${activity.name}?`) && remove.mutate()}>
            Delete
          </button>
        )}
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button type="submit" disabled={save.isPending}>{activity ? "Save" : "Add activity"}</button>
      </div>
    </form>
  );
}
