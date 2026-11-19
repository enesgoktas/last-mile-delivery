import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

const base =
  "inline-flex items-center gap-1.5 rounded-md font-medium transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent-500 text-white hover:bg-accent-600 active:bg-accent-600",
  secondary:
    "border border-slate-300 text-slate-700 bg-white hover:bg-slate-50 active:bg-slate-100",
  ghost:
    "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  danger:
    "text-red-600 border border-red-200 bg-white hover:bg-red-50",
};

const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-8 px-3 text-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
