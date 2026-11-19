export const SETTINGS_TABS = [
  "Operations",
  "Depots",
  "Couriers",
  "Vehicle Specs",
  "Vehicle Tags",
  "Constraints",
  "API Keys",
  "Webhooks",
  "Users",
  "Audit Log",
] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number];

export const SETTINGS_INPUT_CLASSNAME =
  "w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-800 focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20";
