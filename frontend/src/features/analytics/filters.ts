export const MAX_ANALYTICS_RANGE_DAYS = 92;

export const ANALYTICS_PRESETS = [
  { label: "Today", value: "today" },
  { label: "This Week", value: "week" },
  { label: "This Month", value: "month" },
] as const;

export type AnalyticsPreset = (typeof ANALYTICS_PRESETS)[number]["value"];

export type AnalyticsFilterState = {
  startDate: string;
  endDate: string;
};

export function getBrowserTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function formatDateInputValue(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function buildPresetFilterState(preset: AnalyticsPreset, now = new Date()): AnalyticsFilterState {
  const end = new Date(now);
  end.setHours(0, 0, 0, 0);

  const start = new Date(end);
  if (preset === "week") {
    start.setDate(end.getDate() - 6);
  } else if (preset === "month") {
    start.setDate(end.getDate() - 29);
  }

  return {
    startDate: formatDateInputValue(start),
    endDate: formatDateInputValue(end),
  };
}

export function buildAnalyticsQueryParams(
  filters: AnalyticsFilterState,
  timezone = getBrowserTimeZone(),
) {
  return {
    date_from: `${filters.startDate}T00:00:00`,
    date_to: `${filters.endDate}T23:59:59.999`,
    timezone,
  };
}

export function getAnalyticsFilterValidation(filters: AnalyticsFilterState) {
  if (!filters.startDate || !filters.endDate) {
    return { isValid: false, error: "Choose both a start date and an end date.", totalDays: 0 };
  }

  const start = new Date(`${filters.startDate}T00:00:00`);
  const end = new Date(`${filters.endDate}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return { isValid: false, error: "Choose valid calendar dates.", totalDays: 0 };
  }
  if (start > end) {
    return { isValid: false, error: "Start date must be on or before the end date.", totalDays: 0 };
  }

  const millisPerDay = 24 * 60 * 60 * 1000;
  const totalDays = Math.floor((end.getTime() - start.getTime()) / millisPerDay) + 1;
  if (totalDays > MAX_ANALYTICS_RANGE_DAYS) {
    return {
      isValid: false,
      error: `Reports are capped at ${MAX_ANALYTICS_RANGE_DAYS} days for synchronous downloads.`,
      totalDays,
    };
  }

  return { isValid: true, error: null, totalDays };
}
