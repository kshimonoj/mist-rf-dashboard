"use client";

import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { fetchSettings } from "@/lib/api";

interface TimezoneContextValue {
  timezone: string;
  setTimezone: (tz: string) => void;
}

const TimezoneContext = createContext<TimezoneContextValue>({
  timezone: "Asia/Tokyo",
  setTimezone: () => {},
});

export const useTimezone = () => useContext(TimezoneContext);

export function Providers({ children }: { children: ReactNode }) {
  const [timezone, setTimezone] = useState("Asia/Tokyo");

  useEffect(() => {
    fetchSettings()
      .then((s) => { if (s.timezone) setTimezone(s.timezone); })
      .catch(() => {});
  }, []);

  return (
    <TimezoneContext.Provider value={{ timezone, setTimezone }}>
      {children}
    </TimezoneContext.Provider>
  );
}
