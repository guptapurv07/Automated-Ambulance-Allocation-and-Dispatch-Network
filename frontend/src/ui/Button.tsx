import { clsx } from "clsx";
import { ArrowRight } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Tooltip } from "./Tooltip";

/* Buttons: square, flat, 36–40 px. primary = ink; critical = solid red for the most urgent action;
   warning = amber outline for less urgent ones; secondary = white with a hard
   border; text = no box, underline on hover. ("ink" and "muted" are kept as names for older call sites.) */
type Variant = "primary" | "ink" | "critical" | "warning" | "secondary" | "muted" | "ghost" | "danger" | "text";
type Size = "sm" | "md";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium select-none " +
  "transition-[background-color,border-color,color] transition-ui disabled:pointer-events-none disabled:opacity-45";
const variants: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-ink-hover",
  ink: "bg-ink text-white hover:bg-ink-hover",
  critical: "bg-critical text-white hover:bg-[#B42318]",
  warning: "border border-warning bg-panel text-warning hover:bg-warning-tint",
  secondary: "border border-line-strong bg-panel text-ink hover:bg-panel-muted",
  muted: "border border-line-strong bg-panel text-ink hover:bg-panel-muted",
  ghost: "text-ink hover:bg-panel-muted",
  danger: "bg-critical text-white hover:bg-[#B42318]",
  text: "text-ink underline-offset-4 hover:underline",
};
const sizes: Record<Size, string> = {
  sm: "h-9 px-3 text-[14px]",
  md: "h-10 px-4 text-[14px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  /** trailing arrow, e.g. "See all →" */
  arrow?: boolean;
  /** keyboard shortcut shown at the end, e.g. "a" */
  kbd?: string;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, arrow, kbd, className, children, type = "button", ...rest },
  ref,
) {
  const onDark = variant === "primary" || variant === "ink" || variant === "critical" || variant === "danger";
  return (
    <button ref={ref} type={type}
      className={clsx(base, variants[variant], variant === "text" ? "h-auto px-0 text-[14px]" : sizes[size], className)} {...rest}>
      {icon}
      {children}
      {arrow && <ArrowRight size={16} strokeWidth={1.5} aria-hidden />}
      {kbd && <Kbd onDark={onDark}>{kbd}</Kbd>}
    </button>
  );
});

type IconVariant = "muted" | "raised" | "ghost" | "overlay";

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;           // required: tooltip and aria-label
  icon: ReactNode;
  variant?: IconVariant;
  size?: "xs" | "sm" | "md";
  active?: boolean;
  shortcut?: string;
}

const iconVariants: Record<IconVariant, string> = {
  muted: "border border-line-strong bg-panel text-muted hover:bg-panel-muted hover:text-ink",
  raised: "border border-line-strong bg-panel text-muted hover:bg-panel-muted hover:text-ink",
  ghost: "text-muted hover:bg-panel-muted hover:text-ink",
  overlay: "text-ink hover:bg-panel-muted",
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, variant = "muted", size = "sm", active, shortcut, className, type = "button", ...rest },
  ref,
) {
  return (
    <Tooltip content={label} shortcut={shortcut}>
      <button
        ref={ref}
        type={type}
        aria-label={label}
        aria-pressed={active}
        className={clsx(
          "inline-flex shrink-0 items-center justify-center transition-[background-color,border-color,color] transition-ui",
          "disabled:pointer-events-none disabled:opacity-45",
          iconVariants[variant],
          active && "bg-panel-muted text-ink",
          size === "xs" ? "size-8" : size === "sm" ? "size-9" : "size-10",
          className,
        )}
        {...rest}
      >
        {icon}
      </button>
    </Tooltip>
  );
});

export function Kbd({ children, className, onDark }: { children: ReactNode; className?: string; onDark?: boolean }) {
  return (
    <kbd
      className={clsx(
        "num inline-flex h-5 min-w-5 items-center justify-center px-1 text-micro font-medium",
        onDark ? "border border-white/40 text-current" : "border border-line bg-panel-muted text-muted",
        className,
      )}
    >
      {children}
    </kbd>
  );
}
