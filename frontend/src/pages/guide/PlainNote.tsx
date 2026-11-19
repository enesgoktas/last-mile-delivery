import type { ReactNode } from "react";

type PlainNoteProps = {
  children: ReactNode;
};

/**
 * Inline "Plain English" callout used to drop a one-line restatement
 * after a technical paragraph. Quiet styling — no icons, no colors,
 * just an italic margin block with a small label.
 */
export function PlainNote({ children }: PlainNoteProps) {
  return (
    <aside className="my-4 border-l-2 border-slate-200 pl-4 text-slate-600 text-sm">
      <span className="font-mono text-xs uppercase tracking-wider text-slate-400 mr-2">
        Plain English
      </span>
      <span className="italic">{children}</span>
    </aside>
  );
}
