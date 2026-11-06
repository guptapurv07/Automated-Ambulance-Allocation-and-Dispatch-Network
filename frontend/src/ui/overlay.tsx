import * as D from "@radix-ui/react-dialog";
import * as M from "@radix-ui/react-dropdown-menu";
import { clsx } from "clsx";
import { Check, X } from "lucide-react";
import type { ReactNode } from "react";

const overlay = "fixed inset-0 z-40 bg-[rgb(15_18_20/0.32)]";

/* ---------------------------------------------------------------- dialog */

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  width = "max-w-md",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className={overlay} />
        <D.Content
          className={clsx(
            "fixed left-1/2 top-[18vh] z-50 w-[calc(100vw-32px)] -translate-x-1/2 border border-line-strong bg-panel",
            width,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="grid gap-1">
              <D.Title className="text-title font-semibold">{title}</D.Title>
              {description && <D.Description className="text-dense text-muted">{description}</D.Description>}
            </div>
            <D.Close aria-label="Close" className="p-1.5 text-muted hover:bg-panel-muted hover:text-ink">
              <X size={16} strokeWidth={1.5} />
            </D.Close>
          </div>
          {children && <div className="px-5 py-4 text-body">{children}</div>}
          {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

/* ---------------------------------------------------------------- right-side drawer */

export function Drawer({
  open,
  onOpenChange,
  title,
  children,
  footer,
  width = "w-[460px]",
  side = "right",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
  side?: "left" | "right";
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className={overlay} />
        <D.Content
          aria-describedby={undefined}
          className={clsx(
            "fixed inset-y-0 z-50 flex max-w-full flex-col bg-panel",
            side === "right" ? "right-0 border-l border-line-strong data-[state=closed]:translate-x-full" : "left-0 border-r border-line-strong data-[state=closed]:-translate-x-full",
            "transition-transform transition-ui",
            width,
          )}
        >
          <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-5">
            <D.Title className="text-title font-semibold">{title}</D.Title>
            <D.Close aria-label="Close" className="p-1.5 text-muted hover:bg-panel-muted hover:text-ink">
              <X size={18} strokeWidth={1.5} />
            </D.Close>
          </div>
          <div className="flex-1 overflow-auto px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

/* ---------------------------------------------------------------- dropdown menu */

export type MenuItem =
  | { type?: "item"; label: string; onSelect: () => void; icon?: ReactNode; shortcut?: string; danger?: boolean; disabled?: boolean }
  | { type: "check"; label: string; checked: boolean; onSelect: () => void }
  | { type: "separator" }
  | { type: "label"; label: string };

export function Menu({ trigger, items, align = "end" }: { trigger: ReactNode; items: MenuItem[]; align?: "start" | "end" }) {
  const row = "relative flex h-9 cursor-default select-none items-center gap-2.5 px-3 text-body outline-none data-[highlighted]:bg-panel-muted data-[disabled]:opacity-45";
  return (
    <M.Root>
      <M.Trigger asChild>{trigger}</M.Trigger>
      <M.Portal>
        <M.Content align={align} sideOffset={6} className="z-50 min-w-52 border border-line-strong bg-panel p-1 shadow-float">
          {items.map((it, i) => {
            if (it.type === "separator") return <M.Separator key={i} className="mx-2 my-1 h-px bg-border" />;
            if (it.type === "label") return <M.Label key={i} className="px-3 pb-1 pt-1.5 text-meta text-muted">{it.label}</M.Label>;
            if (it.type === "check")
              return (
                <M.CheckboxItem key={i} checked={it.checked} onSelect={it.onSelect} className={clsx(row, "pl-9")}>
                  <M.ItemIndicator className="absolute left-3 text-ink">
                    <Check size={16} strokeWidth={1.75} />
                  </M.ItemIndicator>
                  {it.label}
                </M.CheckboxItem>
              );
            return (
              <M.Item key={i} onSelect={it.onSelect} disabled={it.disabled} className={clsx(row, it.danger && "text-critical-text")}>
                {it.icon && <span className="text-muted">{it.icon}</span>}
                {it.label}
                {it.shortcut && <span className="num ml-auto pl-4 text-meta text-muted">{it.shortcut}</span>}
              </M.Item>
            );
          })}
        </M.Content>
      </M.Portal>
    </M.Root>
  );
}
