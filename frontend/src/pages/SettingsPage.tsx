import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { isAdminLikeRole } from "../config/roles";
import { useAuth } from "../state/AuthContext";
import { ApiKeysTab } from "./settings/ApiKeysTab";
import { AuditLogTab } from "./settings/AuditLogTab";
import { ConstraintsTab } from "./settings/ConstraintsTab";
import { CouriersTab } from "./settings/CouriersTab";
import { DepotsTab } from "./settings/DepotsTab";
import { OperationsPolicyTab } from "./settings/OperationsPolicyTab";
import { SETTINGS_TABS, type SettingsTab } from "./settings/shared";
import { UsersTab } from "./settings/UsersTab";
import { VehicleSpecsTab } from "./settings/VehicleSpecsTab";
import { VehicleTagsTab } from "./settings/VehicleTagsTab";
import { WebhooksTab } from "./settings/WebhooksTab";
import { Tabs } from "../components/ui/Tabs";

export function SettingsPage() {
  const { role } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") as SettingsTab | null;
  const validTab = tabParam && (SETTINGS_TABS as readonly string[]).includes(tabParam) ? tabParam : null;
  const [activeTab, setActiveTab] = useState<SettingsTab>(validTab ?? "Operations");

  function handleTabChange(tab: string) {
    setActiveTab(tab as SettingsTab);
    setSearchParams({ tab }, { replace: true });
  }

  if (!isAdminLikeRole(role)) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-slate-400">Admin access required.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 pt-5 pb-0 shrink-0 border-b border-slate-200">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-base font-semibold text-slate-900">Settings</h1>
        </div>
        <Tabs
          tabs={SETTINGS_TABS.map((t) => ({ id: t, label: t }))}
          active={activeTab}
          onChange={handleTabChange}
        />
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 thin-scrollbar">
        {activeTab === "Operations"    && <OperationsPolicyTab />}
        {activeTab === "Depots"        && <DepotsTab />}
        {activeTab === "Couriers"      && <CouriersTab />}
        {activeTab === "Vehicle Specs" && <VehicleSpecsTab />}
        {activeTab === "Vehicle Tags"  && <VehicleTagsTab />}
        {activeTab === "Constraints"   && <ConstraintsTab />}
        {activeTab === "API Keys"      && <ApiKeysTab />}
        {activeTab === "Webhooks"      && <WebhooksTab />}
        {activeTab === "Users"         && <UsersTab />}
        {activeTab === "Audit Log"     && <AuditLogTab />}
      </div>
    </div>
  );
}
