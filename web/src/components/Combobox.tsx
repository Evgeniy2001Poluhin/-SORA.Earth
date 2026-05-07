import { useEffect, useMemo, useRef, useState } from "react";

type Props = {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  groups?: Record<string, string>;
  placeholder?: string;
};

export default function Combobox({ value, onChange, options, groups, placeholder }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return options.filter(o => !s || o.toLowerCase().includes(s));
  }, [q, options]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => { setHi(0); }, [q, open]);

  const pick = (v: string) => { onChange(v); setOpen(false); setQ(""); };

  const grouped = useMemo(() => {
    if (!groups) return [{ label: "", items: filtered }];
    const map: Record<string, string[]> = {};
    filtered.forEach(o => {
      const g = groups[o] || "Other";
      (map[g] = map[g] || []).push(o);
    });
    return Object.entries(map).map(([label, items]) => ({ label, items }));
  }, [filtered, groups]);

  return (
    <div className="cb" ref={ref}>
      <button type="button" className="cb-trigger" onClick={() => setOpen(o => !o)}>
        <span>{value || placeholder || "Select"}</span>
        <span className="cb-caret">v</span>
      </button>
      {open && (
        <div className="cb-pop">
          <input
            className="cb-search"
            autoFocus
            placeholder="Search"
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => {
              if (e.key === "ArrowDown") {
                setHi(h => Math.min(h + 1, filtered.length - 1));
                e.preventDefault();
              } else if (e.key === "ArrowUp") {
                setHi(h => Math.max(h - 1, 0));
                e.preventDefault();
              } else if (e.key === "Enter") {
                if (filtered[hi]) pick(filtered[hi]);
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
          />
          <div className="cb-list">
            {grouped.map(g => (
              <div key={g.label}>
                {g.label && <div className="cb-group">{g.label}</div>}
                {g.items.map(o => {
                  const idx = filtered.indexOf(o);
                  return (
                    <div
                      key={o}
                      className={"cb-item" + (o === value ? " sel" : "") + (idx === hi ? " hi" : "")}
                      onMouseEnter={() => setHi(idx)}
                      onClick={() => pick(o)}
                    >{o}</div>
                  );
                })}
              </div>
            ))}
            {!filtered.length && <div className="cb-empty">No matches</div>}
          </div>
        </div>
      )}
    </div>
  );
}
