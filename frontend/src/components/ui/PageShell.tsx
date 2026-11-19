import type { ReactNode } from "react";

interface PageShellProps {
  children: ReactNode;
  className?: string;
}

export function PageShell({ children, className = "" }: PageShellProps) {
  return (
    <div className={`flex-1 overflow-y-auto ${className}`}>
      <div className="px-6 py-5 max-w-screen-xl">
        {children}
      </div>
    </div>
  );
}
