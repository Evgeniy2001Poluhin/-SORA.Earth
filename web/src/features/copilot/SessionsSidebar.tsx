import { useCallback, useEffect, useState } from "react";
import { copilotApi, type CopilotSessionSummary } from "../../api/endpoints/copilot";

interface Props {
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  tick?: number;
  refreshToken?: number;
}

const LS_KEY = "copilot.sidebar.collapsed";

export function SessionsSidebar({ currentId, onSelect, onNew, tick = 0, refreshToken = 0 }: Props) {
  const [sessions, setSessions] = useState<CopilotSessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_KEY) === "1"; } catch { return false; }
  });

  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try { localStorage.setItem(LS_KEY, next ? "1" : "0"); } catch { /* not fatal */ }
      return next;
    });
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await copilotApi.listSessions();
      setSessions(r.sessions || []);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load, tick, refreshToken]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm("Delete this chat?")) return;
    try {
      await copilotApi.deleteSession(id);
      setSessions((arr) => arr.filter((s) => s.id !== id));
    } catch { /* not fatal */ }
  };

  const fmtTime = (ms: number) => {
    const diff = Date.now() - ms;
    const m = Math.floor(diff / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + "m";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h";
    return Math.floor(h / 24) + "d";
  };

  if (collapsed) {
    return (
      <aside className="sessions-sidebar sessions-sidebar--collapsed">
        <button className="sessions-toggle" onClick={toggle} title="Show chats" aria-label="Show chats">
          <span className="sessions-toggle-icon">›</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="sessions-sidebar">
      <div className="sessions-header">
        <span className="sessions-title">Chats</span>
        <div className="sessions-actions">
          <button className="sessions-new" onClick={onNew} title="New chat" aria-label="New chat">+</button>
          <button className="sessions-toggle" onClick={toggle} title="Hide chats" aria-label="Hide chats">
            <span className="sessions-toggle-icon">‹</span>
          </button>
        </div>
      </div>
      <div className="sessions-list">
        {loading && sessions.length === 0 ? (
          <div className="sessions-empty">Loading…</div>
        ) : sessions.length === 0 ? (
          <div className="sessions-empty">No chats yet</div>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className={"sessions-row" + (s.id === currentId ? " is-active" : "")}
              onClick={() => onSelect(s.id)}
            >
              <div className="sessions-row-main">
                <div className="sessions-row-title">{s.title || "Untitled"}</div>
                <div className="sessions-row-meta">{fmtTime(s.updated_at)}</div>
              </div>
              <button
                className="sessions-del"
                onClick={(e) => handleDelete(e, s.id)}
                title="Delete"
                aria-label="Delete chat"
              >{String.fromCharCode(215)}</button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
