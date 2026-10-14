import type { SVGProps } from "react";

type Props = SVGProps<SVGSVGElement> & { size?: number };

export function AgentIcon({ size = 24, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <rect x="5" y="7" width="14" height="12" rx="4" />
      <path d="M12 4v3M2.8 13h2.1M19 13h2.2" />
      <circle cx="9.5" cy="12.5" r=".95" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="12.5" r=".95" fill="currentColor" stroke="none" />
      <path d="M9.5 16h5" />
    </svg>
  );
}

export function WorkspaceIcon({ size = 24, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M3.5 7.5a2 2 0 0 1 2-2h4.1l2 2h6.9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
      <path d="M4 10h16" />
    </svg>
  );
}

export function TasksIcon({ size = 24, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <rect x="5" y="3.5" width="14" height="17" rx="3" />
      <path d="m8.5 10 1.6 1.6L13 8.7M14.5 11h2M8.5 16h8" />
    </svg>
  );
}

export function RunEntryIcon({ size = 24, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <rect x="2.5" y="4" width="19" height="16" rx="3" />
      <path d="M14 4v16M8 12h10m-3-3 3 3-3 3" />
    </svg>
  );
}

export function RunStateIcon({ status }: { status: string }) {
  const glyph = {
    succeeded: <path d="m5.5 9 2.5 2.5 5-5" />,
    failed: <path d="M5.5 5.5 12.5 12.5M12.5 5.5 5.5 12.5" />,
    running: <path d="M9 2a7 7 0 1 1-7 7" />,
    queued: <path d="M9 3v6l3 2M2.5 9a6.5 6.5 0 1 0 6.5-6.5" />,
    cancelling: <path d="M5 5h8v8H5z" />,
    cancelled: <path d="M5 5h8v8H5z" />,
  }[status];
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      {glyph ?? <circle cx="9" cy="9" r="2" />}
    </svg>
  );
}
