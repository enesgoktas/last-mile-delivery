import type { ReactNode } from "react";

interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  width?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  getKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  emptyMessage?: string;
  loading?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  getKey,
  onRowClick,
  selectedKey,
  emptyMessage = "No data",
  loading = false,
}: DataTableProps<T>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full text-sm text-left border-collapse">
        <thead>
          <tr className="border-b border-slate-200">
            {columns.map((col) => (
              <th
                key={col.key}
                className={`py-2.5 px-3 text-xs font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap ${col.width ?? ""}`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: 6 }).map((_, i) => (
              <tr key={i} className="border-b border-slate-100">
                {columns.map((col) => (
                  <td key={col.key} className="py-2.5 px-3">
                    <div className="h-4 bg-slate-100 rounded animate-pulse" style={{ width: "60%" }} />
                  </td>
                ))}
              </tr>
            ))
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="py-16 text-center text-sm text-slate-400">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const key = getKey(row);
              const isSelected = selectedKey === key;
              return (
                <tr
                  key={key}
                  onClick={() => onRowClick?.(row)}
                  className={`border-b border-slate-100 transition-colors duration-100 ${
                    onRowClick ? "cursor-pointer" : ""
                  } ${
                    isSelected
                      ? "bg-orange-50"
                      : onRowClick
                      ? "hover:bg-slate-50"
                      : ""
                  }`}
                >
                  {columns.map((col) => (
                    <td key={col.key} className={`py-2.5 px-3 ${col.width ?? ""}`}>
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
