// Small stroke icons for chips (inline SVG, no icon dependency).
const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function CalendarIcon() {
  return (
    <svg {...base}>
      <rect x="3.5" y="5" width="17" height="15" rx="2.5" />
      <path d="M3.5 9.5h17M8 3v4M16 3v4" />
      <path d="M7.5 13h2M11 13h2M14.5 13h2M7.5 16.5h2M11 16.5h2" />
    </svg>
  );
}

export function FolderIcon() {
  return (
    <svg {...base}>
      <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
    </svg>
  );
}

export function PersonIcon() {
  return (
    <svg {...base} fill="currentColor" stroke="none">
      <circle cx="12" cy="9" r="4" />
      <path d="M4.5 20c.8-3.6 3.9-5.5 7.5-5.5s6.7 1.9 7.5 5.5z" />
    </svg>
  );
}

export function SendIcon() {
  return (
    <svg {...base}>
      <path d="M20 4 9.5 14.5M20 4l-6.5 16-4-5.5L4 10.5z" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg {...base}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function WaveIcon() {
  return (
    <svg {...base}>
      <path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg {...base}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </svg>
  );
}

export function NoteIcon() {
  return (
    <svg {...base}>
      <rect x="6" y="3.5" width="12" height="17" rx="2.5" />
      <path d="M9.5 8.5h5M9.5 12h5M9.5 15.5h3" />
    </svg>
  );
}

export function MicIcon() {
  return (
    <svg {...base}>
      <rect x="9" y="3.5" width="6" height="11" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" />
    </svg>
  );
}
