import { useEffect, useState } from "react";

export type TocItem = {
  id: string;
  number: string;
  label: string;
};

type GuideTocProps = {
  items: TocItem[];
};

export function GuideToc({ items }: GuideTocProps) {
  const [activeId, setActiveId] = useState<string>(items[0]?.id ?? "");

  useEffect(() => {
    const observers: IntersectionObserver[] = [];
    const visible = new Set<string>();

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            visible.add(entry.target.id);
          } else {
            visible.delete(entry.target.id);
          }
        }
        // Pick the first visible section in document order.
        for (const item of items) {
          if (visible.has(item.id)) {
            setActiveId(item.id);
            return;
          }
        }
      },
      {
        rootMargin: "-20% 0px -60% 0px",
        threshold: 0,
      },
    );

    for (const item of items) {
      const el = document.getElementById(item.id);
      if (el) observer.observe(el);
    }
    observers.push(observer);

    return () => {
      observers.forEach((o) => o.disconnect());
    };
  }, [items]);

  return (
    <nav aria-label="Table of contents" className="text-sm">
      <p className="text-xs font-mono uppercase tracking-wider text-slate-400 mb-3">
        Contents
      </p>
      <ol className="space-y-1">
        {items.map((item) => {
          const isActive = item.id === activeId;
          return (
            <li key={item.id}>
              <a
                href={`#${item.id}`}
                className={[
                  "block py-1 leading-snug transition-colors",
                  isActive
                    ? "text-slate-900 font-medium"
                    : "text-slate-500 hover:text-slate-800",
                ].join(" ")}
              >
                <span className="font-mono text-xs text-slate-400 mr-2">
                  {item.number}
                </span>
                {item.label}
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
