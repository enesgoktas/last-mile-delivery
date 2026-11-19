import type { ReactNode } from "react";

type ContextDrawerProps = {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
};

export function ContextDrawer({
  open,
  title,
  subtitle,
  onClose,
  children,
}: ContextDrawerProps) {
  if (!open) {
    return null;
  }

  return (
    <aside className="w-[380px] shrink-0 border-l border-gray-200 bg-white shadow-xl flex flex-col overflow-hidden slide-in-panel">
      <div className="px-5 py-4 border-b border-gray-100 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {subtitle ? <p className="mt-1 text-xs text-gray-500">{subtitle}</p> : null}
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          aria-label="Close drawer"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="flex-1 overflow-y-auto thin-scrollbar">{children}</div>
    </aside>
  );
}
