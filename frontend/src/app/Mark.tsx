/** The logo mark: a square frame with a cross. */
export function Mark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden className="shrink-0 text-text">
      <rect x="1.5" y="1.5" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 4.75v6.5M4.75 8h6.5" stroke="currentColor" strokeWidth="1.75" />
    </svg>
  );
}
