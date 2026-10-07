/**
 * A small decorative illustration for empty states: a stack of job cards with a magnifier. Inline SVG in the Atlas
 * colours (it follows light and dark), no image file, no network. It is hidden from screen readers; the words next
 * to it say everything.
 */
export function EmptyArt({ size = 112 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 112 112" fill="none" aria-hidden="true" focusable="false" style={{ display: "block", margin: "0 auto 12px" }}>
      <rect x="18" y="22" width="64" height="20" rx="6" fill="var(--accent-soft)" stroke="var(--line-strong)" />
      <rect x="26" y="29" width="22" height="6" rx="3" fill="var(--accent)" opacity="0.55" />
      <rect x="12" y="46" width="64" height="20" rx="6" fill="var(--surface)" stroke="var(--line-strong)" />
      <rect x="20" y="53" width="30" height="6" rx="3" fill="var(--line-strong)" />
      <rect x="22" y="70" width="64" height="20" rx="6" fill="var(--surface)" stroke="var(--line-strong)" />
      <rect x="30" y="77" width="18" height="6" rx="3" fill="var(--line-strong)" />
      <circle cx="82" cy="70" r="14" fill="var(--surface)" stroke="var(--accent)" strokeWidth="3" />
      <path d="M92 80 L102 90" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}
