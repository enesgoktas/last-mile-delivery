import type { UserRole } from "../types";

// Admin-like = can manage settings, invite users. COMPANY_ADMIN is
// intentionally NOT here — they're per-tenant read-only.
export const ADMIN_LIKE_ROLES = new Set<UserRole>(["OWNER", "ADMIN"]);
// Backoffice = anyone allowed into the dispatcher dashboard, including
// the read-only COMPANY_ADMIN.
export const BACKOFFICE_ROLES = new Set<UserRole>([
  "OWNER",
  "ADMIN",
  "DISPATCHER",
  "COMPANY_ADMIN",
]);
export const PLATFORM_ROLES = new Set<UserRole>(["SUPERADMIN"]);

export function isAdminLikeRole(role: UserRole | null | undefined): boolean {
  return !!role && ADMIN_LIKE_ROLES.has(role);
}

export function isBackofficeRole(role: UserRole | null | undefined): boolean {
  return !!role && BACKOFFICE_ROLES.has(role);
}

export function isSuperadminRole(role: UserRole | null | undefined): boolean {
  return !!role && PLATFORM_ROLES.has(role);
}

export function isReadOnlyTenantRole(role: UserRole | null | undefined): boolean {
  return role === "COMPANY_ADMIN";
}

// Anyone who is allowed to actually act on dispatch: assign, unassign,
// reassign, compare scenarios, etc. Excludes read-only COMPANY_ADMIN
// and platform-level SUPERADMIN (who manage tenants, not run them).
const DISPATCHER_ACTION_ROLES = new Set<UserRole>(["OWNER", "ADMIN", "DISPATCHER"]);

export function canRunDispatcherActions(role: UserRole | null | undefined): boolean {
  return !!role && DISPATCHER_ACTION_ROLES.has(role);
}
