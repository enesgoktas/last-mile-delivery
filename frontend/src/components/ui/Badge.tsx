type BadgeColor = "green" | "red" | "amber" | "blue" | "slate" | "orange";

interface BadgeProps {
  color: BadgeColor;
  children: React.ReactNode;
  className?: string;
}

const dots: Record<BadgeColor, string> = {
  green: "bg-emerald-500",
  red: "bg-red-500",
  amber: "bg-amber-500",
  blue: "bg-blue-500",
  slate: "bg-slate-400",
  orange: "bg-orange-500",
};

const text: Record<BadgeColor, string> = {
  green: "text-emerald-700",
  red: "text-red-700",
  amber: "text-amber-700",
  blue: "text-blue-700",
  slate: "text-slate-600",
  orange: "text-orange-700",
};

export function Badge({ color, children, className = "" }: BadgeProps) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${text[color]} ${className}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dots[color]}`} />
      {children}
    </span>
  );
}

/** Map order/courier/route status strings to a Badge */
export function StatusBadge({ value }: { value: string }) {
  const map: Record<string, { color: BadgeColor; label: string }> = {
    ONLINE:              { color: "green",  label: "Online" },
    OFFLINE:             { color: "slate",  label: "Offline" },
    ON_BREAK:            { color: "amber",  label: "On break" },
    ACTIVE:              { color: "green",  label: "Active" },
    PLANNED:             { color: "blue",   label: "Planned" },
    DRAFT:               { color: "slate",  label: "Draft" },
    COMPLETED:           { color: "green",  label: "Completed" },
    PENDING:             { color: "amber",  label: "Pending" },
    ASSIGNED:            { color: "blue",   label: "Assigned" },
    IN_TRANSIT:          { color: "blue",   label: "In transit" },
    DELIVERED:           { color: "green",  label: "Delivered" },
    FAILED:              { color: "red",    label: "Failed" },
    CANCELLED:           { color: "slate",  label: "Cancelled" },
    ACKNOWLEDGED:        { color: "slate",  label: "Acknowledged" },
    RESOLVED:            { color: "green",  label: "Resolved" },
    WARNING:             { color: "amber",  label: "Warning" },
    CRITICAL:            { color: "red",    label: "Critical" },
    RETURNED_TO_DEPOT:   { color: "slate",  label: "Returned to depot" },
    RETURN_RECEIVED:     { color: "blue",   label: "Return received" },
    FIRST_MILE:          { color: "amber",  label: "First mile" },
    LINEHAUL:            { color: "blue",   label: "Linehaul" },
    LAST_MILE:           { color: "green",  label: "Last mile" },
  };

  const config = map[value] ?? { color: "slate" as BadgeColor, label: value };
  return <Badge color={config.color}>{config.label}</Badge>;
}
