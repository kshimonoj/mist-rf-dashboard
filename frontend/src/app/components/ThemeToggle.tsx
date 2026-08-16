"use client";

import { useEffect, useState } from "react";

export default function ThemeToggle() {
  const [isDark, setIsDark] = useState(true);

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    const dark = saved !== "light";
    setIsDark(dark);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  }, []);

  const toggle = () => {
    const next = !isDark;
    setIsDark(next);
    const theme = next ? "dark" : "light";
    localStorage.setItem("theme", theme);
    document.documentElement.setAttribute("data-theme", theme);
  };

  return (
    <button
      onClick={toggle}
      className="px-3 py-2 border rounded-lg text-sm transition-all"
      style={{
        borderColor: "var(--border-cyan)",
        color: "var(--cyan)",
      }}
      title={isDark ? "ライトモードに切り替え" : "ダークモードに切り替え"}
      aria-label={isDark ? "ライトモードに切り替え" : "ダークモードに切り替え"}
    >
      {isDark ? "☀️" : "🌙"}
    </button>
  );
}
