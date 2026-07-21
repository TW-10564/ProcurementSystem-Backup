interface IconProps {
  className?: string;
}

const base = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function DashboardIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}

export function ForecastIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M15 7h6v6" />
    </svg>
  );
}

export function AccuracyIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function MismatchIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M12 3.5l9.5 16.5H2.5L12 3.5z" />
      <line x1="12" y1="9.5" x2="12" y2="14" />
      <circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function CompatIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M9.5 14.5l5-5" />
      <path d="M8 12.5l-2 2a3 3 0 0 0 4.2 4.2l2-2" />
      <path d="M16 11.5l2-2a3 3 0 0 0-4.2-4.2l-2 2" />
    </svg>
  );
}

export function DiscontinuationIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M4 7h16" />
      <path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />
      <path d="M9.5 11v5" />
      <path d="M14.5 11v5" />
      <path d="M9 7V4.5h6V7" />
    </svg>
  );
}

export function AdjustmentsIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  );
}

export function NotificationsIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M18 8a6 6 0 0 0-12 0c0 6.5-2.5 8.5-2.5 8.5h17S18 14.5 18 8z" />
      <path d="M13.7 21a2 2 0 0 1-3.4 0" />
    </svg>
  );
}
