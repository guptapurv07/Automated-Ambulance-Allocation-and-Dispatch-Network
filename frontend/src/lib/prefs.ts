import { create } from "zustand";
import { persist } from "zustand/middleware";

/* Per-browser conveniences (localStorage). Nothing here is shared or needed by the server. */

interface Prefs {
  sidebarCollapsed: boolean;
  set: (p: Partial<Omit<Prefs, "set">>) => void;
}

export const usePrefs = create<Prefs>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      set: (p) => set(p),
    }),
    { name: "dispatch-prefs" },
  ),
);
