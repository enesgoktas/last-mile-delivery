interface EmptyStateProps {
  message: string;
  className?: string;
}

export function EmptyState({ message, className = "" }: EmptyStateProps) {
  return (
    <div className={`flex items-center justify-center py-16 ${className}`}>
      <p className="text-sm text-slate-400">{message}</p>
    </div>
  );
}
