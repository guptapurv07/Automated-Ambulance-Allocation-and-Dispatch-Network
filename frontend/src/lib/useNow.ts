import { useEffect, useState } from "react";

/** Wall-clock seconds, refreshed every `everyMs` (for "4 min ago" and waiting times). */
export function useNow(everyMs = 15000) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}
