import * as T from "@radix-ui/react-toast";
import { clsx } from "clsx";
import { X } from "lucide-react";
import { create } from "zustand";

/** Toasts only for action results and errors: bottom-right, at most three, 4 s. */
type Toast = { id: number; kind: "success" | "error"; title: string; detail?: string };

const useToasts = create<{ items: Toast[]; push: (t: Omit<Toast, "id">) => void; drop: (id: number) => void }>((set) => ({
  items: [],
  push: (t) => set((s) => ({ items: [...s.items, { ...t, id: Date.now() + Math.random() }].slice(-3) })),
  drop: (id) => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
}));

export const toast = {
  success: (title: string, detail?: string) => useToasts.getState().push({ kind: "success", title, detail }),
  error: (title: string, detail?: string) => useToasts.getState().push({ kind: "error", title, detail }),
};

export function Toaster() {
  const { items, drop } = useToasts();
  return (
    <T.Provider duration={4000} swipeDirection="right">
      {items.map((t) => (
        <T.Root key={t.id} onOpenChange={(open) => !open && drop(t.id)}
          className="flex items-start gap-3 border border-line-strong bg-panel px-4 py-3 shadow-float">
          <span aria-hidden className={clsx("dot mt-[6px] size-2 shrink-0", t.kind === "error" ? "bg-critical" : "bg-ok")} />
          <div className="grid flex-1 gap-0.5">
            <T.Title className="text-dense font-medium text-text">{t.title}</T.Title>
            {t.detail && <T.Description className="text-meta text-muted">{t.detail}</T.Description>}
          </div>
          <T.Close aria-label="Dismiss" className="p-1 text-muted hover:bg-panel-muted hover:text-ink">
            <X size={14} strokeWidth={1.5} />
          </T.Close>
        </T.Root>
      ))}
      <T.Viewport className="fixed bottom-4 right-4 z-[60] flex w-[360px] max-w-[calc(100vw-32px)] flex-col gap-2 outline-none" />
    </T.Provider>
  );
}
