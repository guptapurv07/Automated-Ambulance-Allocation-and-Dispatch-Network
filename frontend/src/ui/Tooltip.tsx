import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <T.Provider delayDuration={350} skipDelayDuration={150}>
    {children}
  </T.Provider>
);

export function Tooltip({
  content,
  shortcut,
  side = "top",
  children,
}: {
  content: ReactNode;
  shortcut?: string;
  side?: "top" | "bottom" | "left" | "right";
  children: ReactNode;
}) {
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          className="z-50 flex items-center gap-2 bg-ink px-2.5 py-1.5 text-meta text-white"
        >
          {content}
          {shortcut && <span className="num text-micro text-white/60">{shortcut}</span>}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
