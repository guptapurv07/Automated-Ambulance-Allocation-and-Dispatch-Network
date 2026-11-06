import * as ToggleGroup from "@radix-ui/react-toggle-group";

/** Pill switcher in a muted track (selected = white raised pill). Used to switch a view's filter
 *  (All / Free / Busy), so it is a single-select button group rather than ARIA tabs with panels.
 *  `count` renders a number after the label. */
export function Tabs<V extends string>({
  value,
  onChange,
  tabs,
  label = "View",
}: {
  value: V;
  onChange: (v: V) => void;
  tabs: { value: V; label: string; count?: number }[];
  label?: string;
}) {
  return (
    <ToggleGroup.Root type="single" aria-label={label} value={value} onValueChange={(v) => v && onChange(v as V)}
      className="inline-flex h-10 border border-line-strong bg-panel">
      {tabs.map((t) => (
        <ToggleGroup.Item key={t.value} value={t.value}
          className="group inline-flex items-center gap-2 border-l border-line px-3.5 text-[14px] font-medium text-muted transition-[background-color,color] transition-ui first:border-l-0 hover:bg-panel-muted hover:text-ink data-[state=on]:bg-ink data-[state=on]:text-white">
          {t.label}
          {t.count != null && <span className="num text-meta text-muted group-data-[state=on]:text-white/70">{t.count}</span>}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}
