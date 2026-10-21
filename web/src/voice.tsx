import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, errorMessage, patch, post, uploadVoiceNote, type VoiceNote } from "./api";
import { Empty, hms } from "./ui";
import { useWorkspace } from "./workspace";

type Where = { projectId?: string; taskId?: string; unfiled?: boolean };

function query(w: Where, wsId: string) {
  const p = new URLSearchParams();
  if (w.projectId) p.set("project_id", w.projectId);
  if (w.taskId) p.set("task_id", w.taskId);
  if (w.unfiled) p.set("unfiled", "true");
  if (!w.projectId && !w.taskId) p.set("workspace_id", wsId);
  return p.toString();
}

export function useVoiceNotes(w: Where, enabled = true) {
  const { current: ws } = useWorkspace();
  const pending = (data?: VoiceNote[]) => data?.some((n) => n.transcript_status === "pending");
  return useQuery({
    queryKey: ["voice-notes", w.projectId ?? null, w.taskId ?? null, !!w.unfiled, ws.id],
    queryFn: () => api<VoiceNote[]>(`/voice-notes?${query(w, ws.id)}`),
    enabled,
    // Poll while Whisper is working on something.
    refetchInterval: (q) => (pending(q.state.data) ? 5000 : false),
  });
}

export function useFeatures() {
  return useQuery({
    queryKey: ["features"],
    queryFn: () => api<{ transcription: boolean }>("/features"),
    staleTime: 5 * 60_000,
  });
}

export function useVoiceInvalidation() {
  const qc = useQueryClient();
  return () =>
    Promise.all(["voice-notes", "ideas", "projects"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
}

const duration = (s: number | null) => (s === null ? "" : hms(s).replace(/^0:/, ""));

function when(iso: string, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

/** A list of notes, newest first, each with a player and its transcript. */
export function VoiceNotes({
  where,
  showWhere = false,
  empty = "No voice notes yet.",
}: {
  where: Where;
  showWhere?: boolean;
  empty?: string | null;
}) {
  const notes = useVoiceNotes(where);
  if (notes.isPending) return <p className="muted small">Loading…</p>;
  if (!notes.data?.length) return empty ? <Empty>{empty}</Empty> : null;
  return (
    <ul className="voice-notes">
      {notes.data.map((n) => (
        <li key={n.id}>
          <NoteCard note={n} showWhere={showWhere} />
        </li>
      ))}
    </ul>
  );
}

export function NoteCard({ note, showWhere }: { note: VoiceNote; showWhere?: boolean }) {
  const { current: ws } = useWorkspace();
  const invalidate = useVoiceInvalidation();
  const features = useFeatures();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(note.transcript ?? "");
  useEffect(() => setText(note.transcript ?? ""), [note.transcript]);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<VoiceNote>(`/voice-notes/${note.id}`, body),
    onSuccess: async () => {
      setEditing(false);
      await invalidate();
    },
  });
  const transcribe = useMutation({
    mutationFn: () => post(`/voice-notes/${note.id}/transcribe`),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: () => del(`/voice-notes/${note.id}`), onSuccess: invalidate });
  const err = save.error ?? remove.error ?? transcribe.error;

  return (
    <article className="note">
      <header className="note-head">
        <strong>{note.title || "Voice note"}</strong>
        <span className="muted small">
          {when(note.recorded_at, ws.timezone)}
          {note.duration_seconds !== null && ` · ${duration(note.duration_seconds)}`}
          {note.source === "yog" && " · from Yog"}
          {showWhere && note.project_name && ` · ${note.project_name}`}
          {note.task_ref && ` · ${note.task_ref} ${note.task_title ?? ""}`}
        </span>
      </header>
      <Player src={note.playback_url} label={note.title || "Voice note"} />
      {editing ? (
        <div className="note-transcript">
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} aria-label="Transcript" autoFocus />
          <div className="row">
            <button onClick={() => save.mutate({ transcript: text.trim() || null })} disabled={save.isPending}>Save</button>
            <button className="ghost" onClick={() => { setText(note.transcript ?? ""); setEditing(false); }}>Cancel</button>
          </div>
        </div>
      ) : note.transcript ? (
        <p className="note-transcript pre">{note.transcript}</p>
      ) : note.transcript_status === "pending" ? (
        <p className="note-transcript muted small">Transcribing…</p>
      ) : note.transcript_status === "failed" ? (
        <p className="note-transcript error small">Transcription failed.</p>
      ) : null}
      <div className="note-actions row small">
        <a href={note.download_url}>Download</a>
        {!editing && (
          <button className="linkish" onClick={() => setEditing(true)}>
            {note.transcript ? "Edit transcript" : "Type a transcript"}
          </button>
        )}
        {features.data?.transcription && note.transcript_status !== "pending" && (
          <button className="linkish" onClick={() => transcribe.mutate()} disabled={transcribe.isPending}
            title="Send to the Whisper service">
            {note.transcript ? "Transcribe again" : "Transcribe"}
          </button>
        )}
        <button className="linkish danger-text"
          onClick={() => confirm("Delete this voice note and its audio file? This can't be undone.") && remove.mutate()}>
          Delete
        </button>
      </div>
      {err && <p className="error small">{errorMessage(err)}</p>}
    </article>
  );
}

const SPEEDS = [1, 1.5, 2];

/** The browser's audio player plus 1× / 1.5× / 2×. Seeking uses HTTP range requests. */
export function Player({ src, label }: { src: string; label: string }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [speed, setSpeed] = useState(1);
  useEffect(() => {
    if (ref.current) ref.current.playbackRate = speed;
  }, [speed]);
  return (
    <div className="player">
      <audio ref={ref} controls preload="metadata" src={src} aria-label={label}
        onLoadedMetadata={(e) => (e.currentTarget.playbackRate = speed)} />
      <div className="speed" role="group" aria-label="Playback speed">
        {SPEEDS.map((s) => (
          <button key={s} type="button" className={s === speed ? "on" : undefined} aria-pressed={s === speed}
            onClick={() => setSpeed(s)}>
            {s}×
          </button>
        ))}
      </div>
    </div>
  );
}

/** Pick a file or record in the browser, then upload it to a project, task or new idea. */
export function AddVoiceNote({
  target,
  onDone,
  compact = false,
}: {
  target: { project_id?: string; task_id?: string; new_idea?: { title: string } };
  onDone?: (n: VoiceNote) => void;
  compact?: boolean;
}) {
  const invalidate = useVoiceInvalidation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const send = useMutation({
    mutationFn: ({ blob, name }: { blob: Blob; name: string }) =>
      uploadVoiceNote(blob, name, {
        ...target,
        title: title.trim() || undefined,
        recorded_at: new Date().toISOString(),
      }),
    onSuccess: async (n) => {
      setTitle("");
      await invalidate();
      onDone?.(n);
    },
  });
  const rec = useRecorder((blob, ext) => send.mutate({ blob, name: `Recording.${ext}` }));

  return (
    <div className={`add-note${compact ? " compact" : ""}`}>
      {!compact && (
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional)"
          aria-label="Voice note title" />
      )}
      <input ref={fileRef} type="file" accept="audio/*,.m4a,.mp3,.wav,.webm,.ogg" hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) send.mutate({ blob: f, name: f.name });
          e.target.value = "";
        }} />
      <button type="button" className="ghost" disabled={send.isPending || rec.recording}
        onClick={() => fileRef.current?.click()}>
        {send.isPending ? "Uploading…" : "Upload audio"}
      </button>
      {rec.supported && (
        <button type="button" className={rec.recording ? "danger" : "ghost"} disabled={send.isPending}
          onClick={() => (rec.recording ? rec.stop() : rec.start())}>
          {rec.recording ? `■ Stop ${hms(rec.seconds).replace(/^0:/, "")}` : "● Record"}
        </button>
      )}
      {(send.isError || rec.error) && (
        <p className="error small">{rec.error ?? errorMessage(send.error)}</p>
      )}
    </div>
  );
}

function useRecorder(onBlob: (b: Blob, ext: string) => void) {
  const supported = typeof window !== "undefined" && "MediaRecorder" in window && !!navigator.mediaDevices;
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);
  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const type = ["audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t));
      const r = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      const chunks: Blob[] = [];
      r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      r.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const mime = r.mimeType || type || "audio/webm";
        const ext = mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm";
        onBlob(new Blob(chunks, { type: mime }), ext);
      };
      rec.current = r;
      r.start();
      setSeconds(0);
      setRecording(true);
    } catch {
      setError("Couldn't use the microphone. Check the browser's permission for this site.");
    }
  };
  const stop = () => {
    rec.current?.stop();
    setRecording(false);
  };
  return { supported, recording, seconds, error, start, stop };
}
