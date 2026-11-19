import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

interface SlideOverProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  width?: string;
}

export function SlideOver({
  open,
  onClose,
  title,
  subtitle,
  children,
  width = "w-[420px]",
}: SlideOverProps) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      {/* backdrop */}
      <div
        className="absolute inset-0 bg-black/20 fade-in"
        onClick={onClose}
      />
      {/* panel */}
      <aside
        className={`relative ${width} h-full bg-white border-l border-slate-200 flex flex-col shadow-xl slide-in-panel`}
      >
        <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-slate-100">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
            {subtitle && (
              <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="ml-4 p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors duration-150"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto thin-scrollbar">
          {children}
        </div>
      </aside>
    </div>
  );
}
