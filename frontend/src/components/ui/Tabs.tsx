interface Tab {
  id: string;
  label: string;
  count?: number;
}

interface TabsProps {
  tabs: Tab[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}

export function Tabs({ tabs, active, onChange, className = "" }: TabsProps) {
  return (
    <div className={`flex items-center border-b border-slate-200 ${className}`}>
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={`px-3 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors duration-150 whitespace-nowrap ${
              isActive
                ? "border-accent-500 text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
            }`}
          >
            {tab.label}
            {tab.count !== undefined && tab.count > 0 && (
              <span className={`ml-1.5 text-xs tabular-nums ${isActive ? "text-accent-500" : "text-slate-400"}`}>
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
