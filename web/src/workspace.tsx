import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Workspace } from "./api";

const KEY = "tahgul.workspace";

type Ctx = {
  workspaces: Workspace[];
  current: Workspace;
  setCurrent: (id: string) => void;
};

const WorkspaceContext = createContext<Ctx | null>(null);

function remembered(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ["workspaces"], queryFn: () => api<Workspace[]>("/workspaces") });
  const [id, setId] = useState<string | null>(remembered);

  useEffect(() => {
    try {
      if (id) localStorage.setItem(KEY, id);
    } catch {
      /* private mode: just don't remember */
    }
  }, [id]);

  if (q.isPending) return <div className="splash" aria-busy="true" />;
  if (q.isError || !q.data.length)
    return (
      <div className="splash">
        <p>No workspace found.</p>
      </div>
    );

  const current = q.data.find((w) => w.id === id) ?? q.data[0];
  return (
    <WorkspaceContext.Provider value={{ workspaces: q.data, current, setCurrent: setId }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): Ctx {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace outside WorkspaceProvider");
  return ctx;
}
