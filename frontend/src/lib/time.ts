function ensureUtc(utcStr: string): string {
  if (!utcStr.endsWith("Z") && !/[+-]\d{2}:\d{2}$/.test(utcStr)) {
    return utcStr + "Z";
  }
  return utcStr;
}

export const toLocalString = (utcStr: string, tz: string): string =>
  new Date(ensureUtc(utcStr)).toLocaleString("ja-JP", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

export const toLocalTimeShort = (utcStr: string, tz: string): string =>
  new Date(ensureUtc(utcStr)).toLocaleTimeString("ja-JP", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

export const toLocalDateTimeShort = (utcStr: string, tz: string): string => {
  const d = new Date(ensureUtc(utcStr));
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: tz,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return `${get("month")}/${get("day")} ${get("hour")}:${get("minute")}`;
};
