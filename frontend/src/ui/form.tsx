import * as CheckboxP from "@radix-ui/react-checkbox";
import * as SelectP from "@radix-ui/react-select";
import * as SliderP from "@radix-ui/react-slider";
import * as SwitchP from "@radix-ui/react-switch";
import * as ToggleGroup from "@radix-ui/react-toggle-group";
import { clsx } from "clsx";
import { Check, ChevronDown, ChevronUp, Minus, Plus } from "lucide-react";
import { forwardRef, useEffect, useId, useState, type InputHTMLAttributes, type ReactNode } from "react";

/* Inputs and selects: white, 1 px border, 40 px, square; a 2 px focus outline. */
const control =
  "h-10 border border-line-strong bg-panel px-3 text-body text-ink placeholder:text-faint outline-offset-1 " +
  "transition-[border-color,background-color] transition-ui hover:border-ink focus-visible:outline-2 focus-visible:outline-focus " +
  "disabled:opacity-45 aria-[invalid=true]:border-critical";

/* ---------------------------------------------------------------- field */

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="grid gap-1">
      <label htmlFor={htmlFor} className="text-dense text-muted">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-meta text-critical-text">{error}</p>
      ) : (
        hint && <p className="text-meta text-muted">{hint}</p>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- input */

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function Input({ className, invalid, ...rest }, ref) {
    return <input ref={ref} aria-invalid={invalid || undefined} className={clsx(control, "w-full", className)} {...rest} />;
  },
);

/* ---------------------------------------------------------------- number input */

function clamp(v: number, min?: number, max?: number) {
  return Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v));
}
function decimals(step: number) {
  const s = String(step);
  return s.includes(".") ? s.split(".")[1]!.length : 0;
}

export function NumberInput({
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  id,
  disabled,
  width = "w-24",
  label,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  id?: string;
  disabled?: boolean;
  width?: string;
  label?: string;
}) {
  const [text, setText] = useState(value.toFixed(decimals(step)));
  useEffect(() => setText(value.toFixed(decimals(step))), [value, step]);
  const commit = (raw: string) => {
    const n = Number(raw);
    if (raw.trim() === "" || Number.isNaN(n)) return setText(value.toFixed(decimals(step)));
    onChange(Number(clamp(n, min, max).toFixed(decimals(step))));
  };
  const bump = (dir: 1 | -1) => onChange(Number(clamp(value + dir * step, min, max).toFixed(decimals(step))));
  return (
    <div className={clsx("flex h-10 items-stretch overflow-hidden border border-line-strong bg-panel focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-focus", width, disabled && "opacity-45")}>
      <input
        id={id}
        aria-label={label}
        inputMode="decimal"
        disabled={disabled}
        className="num min-w-0 flex-1 bg-transparent px-3 text-body font-medium text-ink outline-none"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit((e.target as HTMLInputElement).value);
          if (e.key === "ArrowUp") (e.preventDefault(), bump(1));
          if (e.key === "ArrowDown") (e.preventDefault(), bump(-1));
        }}
      />
      {unit && <span className="flex items-center pr-1 text-meta text-muted">{unit}</span>}
      <div className="flex flex-col border-l border-line">
        <button type="button" tabIndex={-1} aria-label="Increase" disabled={disabled} onClick={() => bump(1)}
          className="flex flex-1 items-center px-2 text-muted hover:bg-panel-muted hover:text-ink">
          <Plus size={10} strokeWidth={1.75} />
        </button>
        <button type="button" tabIndex={-1} aria-label="Decrease" disabled={disabled} onClick={() => bump(-1)}
          className="flex flex-1 items-center border-t border-line px-2 text-muted hover:bg-panel-muted hover:text-ink">
          <Minus size={10} strokeWidth={1.75} />
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- slider + number, with default tick */

export function SliderField({
  value,
  onChange,
  min,
  max,
  step,
  defaultValue,
  label,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  defaultValue?: number;
  label: string;
  disabled?: boolean;
}) {
  const id = useId();
  const pos = (v: number) => `${((v - min) / (max - min)) * 100}%`;
  return (
    <div className="flex items-center gap-3">
      <div className="relative flex-1">
        <SliderP.Root
          aria-label={label}
          className="relative flex h-5 touch-none select-none items-center"
          min={min}
          max={max}
          step={step}
          value={[value]}
          disabled={disabled}
          onValueChange={(v) => onChange(v[0]!)}
        >
          <SliderP.Track className="relative h-1 grow bg-panel-muted outline outline-1 -outline-offset-1 outline-line">
            <SliderP.Range className="absolute h-full bg-ink" />
          </SliderP.Track>
          {defaultValue != null && (
            <span
              aria-hidden
              title={`Default ${defaultValue}`}
              className="pointer-events-none absolute top-[4px] h-3 w-px bg-muted"
              style={{ left: pos(defaultValue) }}
            />
          )}
          <SliderP.Thumb aria-label={label} className="block h-[18px] w-2.5 border border-ink bg-panel focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus" />
        </SliderP.Root>
      </div>
      <NumberInput id={id} label={label} value={value} onChange={onChange} min={min} max={max} step={step} disabled={disabled} />
    </div>
  );
}

/* ---------------------------------------------------------------- select */

export function Select<T extends string>({
  value,
  onChange,
  options,
  label,
  width = "w-44",
  disabled,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; hint?: string }[];
  label: string;
  width?: string;
  disabled?: boolean;
}) {
  return (
    <SelectP.Root value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <SelectP.Trigger aria-label={label} className={clsx(control, "inline-flex items-center justify-between gap-2", width)}>
        <SelectP.Value />
        <SelectP.Icon className="text-muted">
          <ChevronDown size={14} strokeWidth={1.5} />
        </SelectP.Icon>
      </SelectP.Trigger>
      <SelectP.Portal>
        <SelectP.Content position="popper" sideOffset={4}
          className="z-50 max-h-[min(360px,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] overflow-hidden border border-line-strong bg-panel p-1 shadow-float">
          {/* a long list scrolls inside the window; the arrows show there is more */}
          <SelectP.ScrollUpButton className="flex h-6 items-center justify-center text-muted"><ChevronUp size={14} strokeWidth={1.5} /></SelectP.ScrollUpButton>
          <SelectP.Viewport>
            {options.map((o) => (
              <SelectP.Item key={o.value} value={o.value}
                className="relative flex h-9 cursor-default select-none items-center gap-2 pl-8 pr-3 text-body text-ink outline-none data-[highlighted]:bg-panel-muted">
                <SelectP.ItemIndicator className="absolute left-2.5 text-ink">
                  <Check size={13} strokeWidth={1.75} />
                </SelectP.ItemIndicator>
                <SelectP.ItemText>{o.label}</SelectP.ItemText>
                {o.hint && <span className="ml-auto text-meta text-muted">{o.hint}</span>}
              </SelectP.Item>
            ))}
          </SelectP.Viewport>
          <SelectP.ScrollDownButton className="flex h-6 items-center justify-center text-muted"><ChevronDown size={14} strokeWidth={1.5} /></SelectP.ScrollDownButton>
        </SelectP.Content>
      </SelectP.Portal>
    </SelectP.Root>
  );
}

/* ---------------------------------------------------------------- segmented control */

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  size = "md",
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; title?: string }[];
  label: string;
  size?: "sm" | "md";
}) {
  return (
    <ToggleGroup.Root
      type="single"
      aria-label={label}
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      className={clsx("inline-flex border border-line-strong bg-panel", size === "sm" ? "h-9" : "h-10")}
    >
      {options.map((o) => (
        <ToggleGroup.Item key={o.value} value={o.value} title={o.title}
          className="inline-flex min-w-9 items-center justify-center gap-1.5 border-l border-line px-3 text-[14px] font-medium text-muted transition-[background-color,color] transition-ui first:border-l-0 hover:bg-panel-muted hover:text-ink data-[state=on]:bg-ink data-[state=on]:text-white">
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

/* ---------------------------------------------------------------- switch, checkbox */

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  showLabel = true,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
  showLabel?: boolean;
}) {
  const id = useId();
  return (
    <div className="inline-flex items-center gap-2">
      <SwitchP.Root id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} aria-label={showLabel ? undefined : label}
        className="relative h-5 w-9 shrink-0 border border-line-strong bg-panel-muted transition-colors transition-ui data-[state=checked]:border-ink data-[state=checked]:bg-ink disabled:opacity-45">
        <SwitchP.Thumb className="block size-3.5 translate-x-0.5 border border-line-strong bg-panel transition-transform transition-ui data-[state=checked]:translate-x-[17px] data-[state=checked]:border-panel" />
      </SwitchP.Root>
      {showLabel && (
        <label htmlFor={id} className="text-body text-ink">
          {label}
        </label>
      )}
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  showLabel = true,
  disabled,
}: {
  checked: boolean | "indeterminate";
  onChange: (v: boolean) => void;
  label: string;
  showLabel?: boolean;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="inline-flex items-center gap-2">
      <CheckboxP.Root id={id} checked={checked} disabled={disabled} aria-label={showLabel ? undefined : label}
        onCheckedChange={(v) => onChange(v === true)}
        className="flex size-[18px] shrink-0 items-center justify-center border border-line-strong bg-panel text-white data-[state=checked]:border-ink data-[state=checked]:bg-ink data-[state=indeterminate]:border-ink data-[state=indeterminate]:bg-ink disabled:opacity-45">
        <CheckboxP.Indicator>
          {checked === "indeterminate" ? <Minus size={12} strokeWidth={2} /> : <Check size={12} strokeWidth={2} />}
        </CheckboxP.Indicator>
      </CheckboxP.Root>
      {showLabel && (
        <label htmlFor={id} className="text-dense text-ink">
          {label}
        </label>
      )}
    </div>
  );
}
