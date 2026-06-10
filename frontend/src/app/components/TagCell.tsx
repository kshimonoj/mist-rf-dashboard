"use client";

import { useState, useRef, useEffect } from "react";

const TAG_COLORS = ["#00d4ff", "#7c3aed", "#00ff80", "#facc15", "#ff8c00", "#3b82f6", "#f472b6"];

function tagColor(tag: string): string {
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) % 997;
  return TAG_COLORS[h % TAG_COLORS.length];
}

export function TagBadge({ tag }: { tag: string }) {
  const color = tagColor(tag);
  return (
    <span
      className="px-2 py-0.5 rounded text-xs font-mono border whitespace-nowrap"
      style={{ color, borderColor: color, backgroundColor: `${color}15` }}
    >
      {tag}
    </span>
  );
}

export default function TagCell({
  tags,
  onSave,
}: {
  tags: string[];
  onSave: (tagsStr: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(tags.join(", "));
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setValue(tags.join(", "));
  }, [tags, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const startEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setValue(tags.join(", "));
    setEditing(true);
  };

  const commit = async () => {
    const next = value.trim();
    if (next === tags.join(", ")) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(next);
    } finally {
      setSaving(false);
      setEditing(false);
    }
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={value}
        disabled={saving}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { setValue(tags.join(", ")); setEditing(false); }
        }}
        placeholder="tag1, tag2"
        className="w-40 px-2 py-1 rounded border bg-transparent text-xs font-mono"
        style={{ borderColor: "var(--cyan)", color: "var(--text-primary)" }}
      />
    );
  }

  return (
    <div
      onClick={startEdit}
      className="flex flex-wrap items-center gap-1 cursor-pointer min-w-[60px]"
      title="クリックで編集"
    >
      {tags.length > 0 ? (
        tags.map((t) => <TagBadge key={t} tag={t} />)
      ) : (
        <span className="text-xs font-mono px-2 py-0.5 rounded border border-dashed"
          style={{ color: "var(--text-muted)", borderColor: "var(--chart-grid)" }}>
          +tag
        </span>
      )}
    </div>
  );
}
