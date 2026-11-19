type PaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
};

export function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) return null;

  const pages: (number | "...")[] = [];
  for (let i = 0; i < totalPages; i++) {
    if (i === 0 || i === totalPages - 1 || Math.abs(i - page) <= 1) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== "...") {
      pages.push("...");
    }
  }

  return (
    <div className="flex items-center justify-between pt-4">
      <p className="text-xs text-gray-500">
        {total} result{total !== 1 ? "s" : ""} — page {page + 1} of {totalPages}
      </p>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
          className="px-2.5 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Prev
        </button>
        {pages.map((p, idx) =>
          p === "..." ? (
            <span key={`ellipsis-${idx}`} className="px-1 text-xs text-gray-400">...</span>
          ) : (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              className={`px-2.5 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                p === page
                  ? "bg-brand-50 text-brand-700 border-brand-200"
                  : "text-gray-600 bg-white border-gray-200 hover:bg-gray-50"
              }`}
            >
              {p + 1}
            </button>
          ),
        )}
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="px-2.5 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Next
        </button>
      </div>
    </div>
  );
}
