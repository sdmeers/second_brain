import type { SVGProps } from "react";

const base = (props: SVGProps<SVGSVGElement>) => ({
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  ...props,
});

export const CheckIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)} strokeWidth={3}><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
);
export const GripIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)} fill="currentColor" stroke="none">
    {[6, 12, 18].flatMap((y) => [9, 15].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.6" />))}
  </svg>
);
export const BoltIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" /></svg>
);
export const HourglassIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M6 3h12M6 21h12M7 3v4l5 5-5 5v4M17 3v4l-5 5 5 5v4" /></svg>
);
export const BulbIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M9 18h6M10 21h4M12 3a6 6 0 00-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0012 3z" /></svg>
);
export const InboxIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M3 13l3-8h12l3 8v6H3v-6zM3 13h5l1 3h6l1-3h5" /></svg>
);
export const CalendarIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M8 3v4M16 3v4M3.5 10h17" /></svg>
);
export const BellIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M6 16V11a6 6 0 1112 0v5l1.5 2h-15L6 16zM10 21h4" /></svg>
);
export const ChevronIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M9 6l6 6-6 6" /></svg>
);
export const PlusIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)} strokeWidth={2.5}><path d="M12 5v14M5 12h14" /></svg>
);
export const MicIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21M8.5 21h7" /></svg>
);
export const CloseIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M6 6l12 12M18 6L6 18" /></svg>
);
