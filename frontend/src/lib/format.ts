/** 24-hour local time. Missing values render as "—". */

const pad = (n: number) => String(n).padStart(2, "0");

export function clock(wall: number): string {
  const d = new Date(wall * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** "14:03:22", or "12 Oct 14:03:22" when not today. */
export function timestamp(wall: number, now = Date.now() / 1000): string {
  const d = new Date(wall * 1000);
  const today = new Date(now * 1000);
  if (d.toDateString() === today.toDateString()) return clock(wall);
  return `${d.getDate()} ${d.toLocaleString("en-GB", { month: "short" })} ${clock(wall)}`;
}

export function relative(wall: number, now = Date.now() / 1000): string {
  const s = Math.max(0, Math.round(now - wall));
  if (s < 45) return "just now";
  if (s < 90) return "1 min ago";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 5400) return "1 h ago";
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** The API sends naive local datetimes ("2026-10-26T16:14:00.123456"); seconds since the epoch. */
export const wallOf = (iso: string) => Date.parse(iso) / 1000;

export const minutes = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)} min`);
export const km = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)} km`);
export const coord = (lat: number, lon: number) => `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
