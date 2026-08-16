"use client";

import { ChevronDown } from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";

interface DropdownProps {
  /** ラベル文字列。アイコンのみのトリガーにする場合は省略し ariaLabel を指定する */
  label?: string;
  icon?: ReactNode;
  ariaLabel?: string;
  align?: "left" | "right";
  children: (close: () => void) => ReactNode;
}

/** キーボード操作（Esc で閉じる／外側クリックで閉じる）に対応した汎用ドロップダウン */
export default function Dropdown({ label, icon, ariaLabel, align = "right", children }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div className="relative" ref={containerRef}>
      <button
        ref={triggerRef}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={ariaLabel}
        className="flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-all whitespace-nowrap"
        style={{ borderColor: "var(--border-cyan)", color: "var(--cyan)" }}
      >
        {icon}
        {label && <span className="whitespace-nowrap">{label}</span>}
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute top-full mt-1 z-20 min-w-[11rem] rounded-lg border shadow-xl py-1"
          style={{
            [align === "right" ? "right" : "left"]: 0,
            backgroundColor: "var(--bg-card)",
            borderColor: "var(--border-cyan)",
          }}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
