import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useFocusEffect } from "@react-navigation/native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  getActiveRoute,
  getMyCourierProfile,
  getNextStop,
  logout,
  startRoute,
  updateCourierStatus,
} from "../services/api";
import {
  clearOfflineQueue,
  getOfflineQueue,
  syncOfflineQueue,
  type OfflineQueueItem,
  type QueueSyncConflict,
} from "../services/offlineQueue";
import {
  getLocationTrackingHealth,
  startCourierLocationTracking,
  stopCourierLocationTracking,
  subscribeToLocationTrackingHealth,
  type LocationTrackingHealth,
} from "../services/locationService";
import {
  getPushRegistrationWarning,
  registerCourierPushToken,
  type PushRegistrationStatus,
} from "../services/pushService";
import {
  buildCourierSystemWarnings,
  buildSyncSummary,
  calculateRouteProgress,
  formatRelativeSyncTime,
  isRouteTerminal,
} from "./myRouteHelpers";
import type { CourierRoute, RouteStop, RootStackParamList } from "../types";

type Props = NativeStackScreenProps<RootStackParamList, "MyRoute">;

const POLL_MS = 30_000;

export function MyRouteScreen({ navigation }: Props) {
  const [courierId, setCourierId] = useState<string | null>(null);
  const [route, setRoute] = useState<CourierRoute | null>(null);
  const [nextStop, setNextStop] = useState<RouteStop | null>(null);
  const [dismissedCompletedRouteId, setDismissedCompletedRouteId] = useState<string | null>(null);
  const [queueCount, setQueueCount] = useState(0);
  const [queueItems, setQueueItems] = useState<OfflineQueueItem[]>([]);
  const [showQueueDetails, setShowQueueDetails] = useState(false);
  const [nextRetryAt, setNextRetryAt] = useState<string | null>(null);
  const [lastBackgroundSyncAt, setLastBackgroundSyncAt] = useState<string | null>(null);
  const [lastBackgroundSyncSummary, setLastBackgroundSyncSummary] = useState<string | null>(null);
  const [lastSyncConflicts, setLastSyncConflicts] = useState<QueueSyncConflict[]>([]);
  const [courierStatus, setCourierStatus] = useState<"ONLINE" | "OFFLINE" | "ON_BREAK">("OFFLINE");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [refreshingCompletion, setRefreshingCompletion] = useState(false);
  const [changingStatus, setChangingStatus] = useState(false);
  const [pushTokenState, setPushTokenState] = useState<PushRegistrationStatus | "not_registered">("not_registered");
  const [pushWarning, setPushWarning] = useState<string | null>(null);
  const [locationTrackingHealth, setLocationTrackingHealth] = useState<LocationTrackingHealth>(
    getLocationTrackingHealth(),
  );
  const [nowMs, setNowMs] = useState(() => Date.now());
  const pushRegistrationAttemptedRef = useRef(false);
  const loadRouteInFlightRef = useRef(false);
  const loadRoutePendingRef = useRef(false);
  const syncInFlightRef = useRef(false);
  const completionAlertRouteIdRef = useRef<string | null>(null);

  const loadQueueCount = useCallback(async () => {
    const queue = await getOfflineQueue();
    setQueueCount(queue.length);
    setQueueItems(queue);
    const pendingRetry = queue.find((item) => Date.parse(item.retryAt) > Date.now());
    setNextRetryAt(pendingRetry?.retryAt ?? null);
  }, []);

  useEffect(() => {
    if (queueCount === 0) {
      setShowQueueDetails(false);
    }
  }, [queueCount]);

  useEffect(() => {
    if (!lastBackgroundSyncAt) {
      return;
    }
    const timer = setInterval(() => {
      setNowMs(Date.now());
    }, 5_000);
    return () => clearInterval(timer);
  }, [lastBackgroundSyncAt]);

  const loadRouteContext = useCallback(async () => {
    if (loadRouteInFlightRef.current) {
      loadRoutePendingRef.current = true;
      return;
    }

    loadRouteInFlightRef.current = true;
    try {
      do {
        loadRoutePendingRef.current = false;
        setLoading(true);
        setLoadError(null);
        try {
          const profile = await getMyCourierProfile();
          const activeCourierId = profile.id;
          setCourierId(activeCourierId);
          setCourierStatus(profile.availability_status);
          setPushTokenState((profile.push_token ?? profile.fcm_token) ? "registered" : "not_registered");
          setPushWarning(null);
          const [activeRoute, next] = await Promise.all([
            getActiveRoute(activeCourierId),
            getNextStop(activeCourierId),
          ]);
          setRoute(activeRoute);
          setNextStop(next);
          if (!activeRoute || activeRoute.id !== dismissedCompletedRouteId) {
            setDismissedCompletedRouteId(null);
          }
          await loadQueueCount();
        } catch (error) {
          setLoadError(
            error instanceof Error ? error.message : "Failed to load route. Check your connection.",
          );
        } finally {
          setLoading(false);
        }
      } while (loadRoutePendingRef.current);
    } finally {
      loadRouteInFlightRef.current = false;
    }
  }, [loadQueueCount]);

  useEffect(() => {
    void loadRouteContext();
  }, [loadRouteContext]);

  useEffect(() => {
    const timer = setInterval(() => {
      void loadRouteContext();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [loadRouteContext]);

  useFocusEffect(
    useCallback(() => {
      void loadRouteContext();
    }, [loadRouteContext]),
  );

  useEffect(() => {
    return subscribeToLocationTrackingHealth(setLocationTrackingHealth);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      if (!courierId) return;
      try {
        if (courierStatus === "ONLINE") {
          await startCourierLocationTracking(courierId);
        } else {
          await stopCourierLocationTracking();
        }
      } catch (error) {
        if (!cancelled) {
          Alert.alert(
            "Location tracking",
            error instanceof Error ? error.message : "Unable to start location tracking.",
          );
        }
      }
    };
    void run();

    return () => {
      cancelled = true;
      void stopCourierLocationTracking();
    };
  }, [courierId, courierStatus]);

  useEffect(() => {
    AsyncStorage.getItem("battery_warning_shown")
      .then((shown) => {
        if (!shown) {
          Alert.alert(
            "Battery Usage",
            "This app tracks your location in the background. For best results, disable battery optimization for this app in your device settings.",
            [
              {
                text: "OK",
                onPress: () => {
                  void AsyncStorage.setItem("battery_warning_shown", "true");
                },
              },
            ],
          );
        }
      })
      .catch(() => {
        // Non-fatal if we cannot read from AsyncStorage.
      });
  }, []);

  const onRegisterPushToken = useCallback(async () => {
    if (!courierId) return;
    const result = await registerCourierPushToken(courierId);
    setPushTokenState(result.status);
    setPushWarning(getPushRegistrationWarning(result));
  }, [courierId]);

  useEffect(() => {
    if (!courierId || courierStatus !== "ONLINE") {
      return;
    }
    if (pushRegistrationAttemptedRef.current || pushTokenState === "registered") {
      return;
    }
    pushRegistrationAttemptedRef.current = true;
    void onRegisterPushToken();
  }, [courierId, courierStatus, onRegisterPushToken, pushTokenState]);

  const onStartRoute = () => {
    if (!visibleRoute) return;
    navigation.navigate("LoadingChecklist", { routeId: visibleRoute.id });
  };

  const runQueueSync = useCallback(async (showSummaryAlert: boolean) => {
    if (syncInFlightRef.current) {
      return;
    }
    syncInFlightRef.current = true;
    setSyncing(true);
    try {
      const report = await syncOfflineQueue();
      setLastSyncConflicts(report.conflictItems);
      await loadQueueCount();
      if (report.processed > 0 || report.conflicts > 0) {
        await loadRouteContext();
      }
      if (showSummaryAlert) {
        Alert.alert("Sync finished", buildSyncSummary(report, true));
      } else if (report.processed > 0 || report.deferred > 0 || report.conflicts > 0) {
        setLastBackgroundSyncAt(new Date().toISOString());
        setLastBackgroundSyncSummary(buildSyncSummary(report, true));
      }
    } catch (error) {
      if (showSummaryAlert) {
        Alert.alert("Sync failed", error instanceof Error ? error.message : "Unknown error");
      } else {
        setLastBackgroundSyncAt(new Date().toISOString());
        setLastBackgroundSyncSummary(
          `Background sync failed: ${error instanceof Error ? error.message : "Unknown error"}`,
        );
      }
    } finally {
      syncInFlightRef.current = false;
      setSyncing(false);
    }
  }, [loadQueueCount, loadRouteContext]);

  const onClearQueue = useCallback(() => {
    Alert.alert(
      "Clear offline queue?",
      "This removes all pending offline actions on this device.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Clear Queue",
          style: "destructive",
          onPress: () => {
            void (async () => {
              await clearOfflineQueue();
              await loadQueueCount();
              setLastSyncConflicts([]);
              setLastBackgroundSyncAt(new Date().toISOString());
              setLastBackgroundSyncSummary("Queue cleared manually.");
              Alert.alert("Queue cleared", "Pending offline actions were removed.");
            })().catch((error) => {
              Alert.alert("Unable to clear queue", error instanceof Error ? error.message : "Unknown error");
            });
          },
        },
      ],
    );
  }, [loadQueueCount]);

  useEffect(() => {
    if (queueCount === 0) {
      return;
    }
    void runQueueSync(false);
    const timer = setInterval(() => {
      void runQueueSync(false);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [queueCount, runQueueSync]);

  const onLogout = async () => {
    await stopCourierLocationTracking();
    await logout();
    navigation.replace("Login");
  };

  const onChangeStatus = async (status: "ONLINE" | "OFFLINE" | "ON_BREAK") => {
    if (!courierId) return;
    setChangingStatus(true);
    try {
      const profile = await updateCourierStatus(courierId, status);
      setCourierStatus(profile.availability_status);
      await loadRouteContext();
    } catch (error) {
      Alert.alert("Status update failed", error instanceof Error ? error.message : "Unknown error");
    } finally {
      setChangingStatus(false);
    }
  };

  const onRefreshRouteCompletion = async () => {
    if (!route) return;
    setRefreshingCompletion(true);
    try {
      await loadRouteContext();
      Alert.alert(
        "Route status refreshed",
        "If all stop updates were processed, the route will now show as completed.",
      );
    } finally {
      setRefreshingCompletion(false);
    }
  };

  const visibleRoute = useMemo(() => {
    if (!route || route.id !== dismissedCompletedRouteId) {
      return route;
    }
    return null;
  }, [dismissedCompletedRouteId, route]);
  const visibleNextStop = visibleRoute ? nextStop : null;
  const routeStops = useMemo(
    () => [...(visibleRoute?.stops ?? [])].sort((a, b) => a.sequence_number - b.sequence_number),
    [visibleRoute?.stops],
  );
  const allStopsTerminal = useMemo(() => isRouteTerminal(visibleRoute), [visibleRoute]);
  const routeProgress = useMemo(() => calculateRouteProgress(routeStops), [routeStops]);
  const lastSyncRelativeText = useMemo(() => {
    if (!lastBackgroundSyncAt) {
      return null;
    }
    return formatRelativeSyncTime(lastBackgroundSyncAt, nowMs);
  }, [lastBackgroundSyncAt, nowMs]);

  const systemWarnings = useMemo(
    () =>
      buildCourierSystemWarnings({
        courierStatus,
        platformOs: Platform.OS,
        pushTokenState,
        pushWarning,
        locationTrackingHealth,
      }),
    [courierStatus, locationTrackingHealth, pushTokenState, pushWarning],
  );

  useEffect(() => {
    if (!visibleRoute || !allStopsTerminal) {
      if (!visibleRoute) {
        completionAlertRouteIdRef.current = null;
      }
      return;
    }
    if (completionAlertRouteIdRef.current === visibleRoute.id) {
      return;
    }
    completionAlertRouteIdRef.current = visibleRoute.id;
    Alert.alert(
      "Route completed",
      "You have finished all stops. Great job!",
      [
        {
          text: "OK",
          onPress: () => {
            setDismissedCompletedRouteId(visibleRoute.id);
          },
        },
      ],
    );
  }, [allStopsTerminal, visibleRoute]);

  if (loading && !visibleRoute && !loadError) {
    return (
      <View style={styles.fullscreenCenter}>
        <ActivityIndicator size="large" color="#ff6f3d" />
        <Text style={styles.hint}>Loading route context...</Text>
      </View>
    );
  }

  if (loadError) {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.headerRow}>
          <Text style={styles.title}>My Route</Text>
          <Pressable style={styles.secondaryButton} onPress={onLogout}>
            <Text style={styles.secondaryButtonText}>Logout</Text>
          </Pressable>
        </View>

        <View style={styles.errorContainer}>
          <Text style={styles.errorTitle}>Unable to load route</Text>
          <Text style={styles.errorText}>{loadError}</Text>
          <Pressable style={styles.retryButton} onPress={loadRouteContext}>
            <Text style={styles.retryButtonText}>Retry</Text>
          </Pressable>
        </View>
      </ScrollView>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>My Route</Text>
        <Pressable style={styles.secondaryButton} onPress={onLogout}>
          <Text style={styles.secondaryButtonText}>Logout</Text>
        </Pressable>
      </View>

      <View style={styles.statusCard}>
        <View style={styles.statusTopRow}>
          <View>
            <Text style={styles.statusLabel}>Route Status</Text>
            <Text style={styles.statusValue}>{visibleRoute?.status ?? "NO ACTIVE ROUTE"}</Text>
          </View>
          <View style={[styles.courierBadge, courierStatus === "ONLINE" && styles.courierBadgeOnline, courierStatus === "ON_BREAK" && styles.courierBadgeBreak]}>
            <Text style={styles.courierBadgeText}>{courierStatus.replace("_", " ")}</Text>
          </View>
        </View>

        {systemWarnings.map((warning) => (
          <View key={warning} style={styles.pushWarning}>
            <Text style={styles.pushWarningText}>{warning}</Text>
          </View>
        ))}

        {queueCount > 0 && (
          <View style={styles.syncBanner}>
            <Text style={styles.syncBannerText}>
              {queueCount} action{queueCount === 1 ? "" : "s"} pending sync
              {nextRetryAt ? ` · next at ${new Date(nextRetryAt).toLocaleTimeString()}` : ""}
            </Text>
            <Pressable
              style={styles.syncDetailsButton}
              onPress={() => setShowQueueDetails((current) => !current)}
            >
              <Text style={styles.syncDetailsButtonText}>
                {showQueueDetails ? "Hide Sync Details" : "View Sync Details"}
              </Text>
            </Pressable>
          </View>
        )}

        {visibleNextStop ? (
          <Text style={styles.nextStopHint}>
            Next: #{visibleNextStop.sequence_number} · {visibleNextStop.address}
          </Text>
        ) : null}
        {routeProgress.total > 0 ? (
          <View style={styles.progressBlock}>
            <View style={styles.progressRow}>
              <Text style={styles.progressLabel}>Progress</Text>
              <Text style={styles.progressValue}>
                {routeProgress.terminal}/{routeProgress.total} done
              </Text>
            </View>
            <View style={styles.progressTrack}>
              <View style={[styles.progressFill, { width: `${routeProgress.percent}%` }]} />
            </View>
          </View>
        ) : null}
      </View>

      <View style={styles.actionRow}>
        <Pressable
          style={[
            styles.button,
            (!visibleRoute || visibleRoute.status !== "PLANNED") && styles.buttonDisabled,
          ]}
          onPress={onStartRoute}
          disabled={!visibleRoute || visibleRoute.status !== "PLANNED"}
        >
          <Text style={styles.buttonText}>Start Route</Text>
        </Pressable>
      </View>

      <View style={styles.actionRow}>
        <Pressable
          style={[styles.statusButton, courierStatus === "ONLINE" && styles.statusButtonActive, changingStatus && styles.buttonDisabled]}
          onPress={() => onChangeStatus("ONLINE")}
          disabled={changingStatus || courierStatus === "ONLINE"}
        >
          <Text style={[styles.statusButtonText, courierStatus === "ONLINE" && styles.statusButtonTextActive]}>Online</Text>
        </Pressable>
        <Pressable
          style={[styles.statusButton, courierStatus === "ON_BREAK" && styles.statusButtonBreak, changingStatus && styles.buttonDisabled]}
          onPress={() => onChangeStatus("ON_BREAK")}
          disabled={changingStatus || courierStatus === "ON_BREAK"}
        >
          <Text style={[styles.statusButtonText, courierStatus === "ON_BREAK" && styles.statusButtonTextActive]}>Break</Text>
        </Pressable>
        <Pressable
          style={[styles.statusButton, courierStatus === "OFFLINE" && styles.statusButtonOffline, changingStatus && styles.buttonDisabled]}
          onPress={() => onChangeStatus("OFFLINE")}
          disabled={changingStatus || courierStatus === "OFFLINE"}
        >
          <Text style={[styles.statusButtonText, courierStatus === "OFFLINE" && styles.statusButtonTextActive]}>Offline</Text>
        </Pressable>
      </View>

      {lastSyncConflicts.length > 0 ? (
        <View style={styles.conflictCard}>
          <Text style={styles.conflictTitle}>Sync conflicts detected</Text>
          <Text style={styles.conflictHint}>
            These actions were dropped because stops changed or were reassigned:
          </Text>
          {lastSyncConflicts.slice(0, 5).map((conflict) => (
            <Text key={conflict.itemId} style={styles.conflictItem}>
              • {conflict.actionType.replace(/_/g, " ")}: {conflict.reason}
            </Text>
          ))}
          <Pressable
            style={styles.secondaryButton}
            onPress={() => setLastSyncConflicts([])}
          >
            <Text style={styles.secondaryButtonText}>Dismiss</Text>
          </Pressable>
        </View>
      ) : null}

      {lastBackgroundSyncSummary ? (
        <View style={styles.syncCard}>
          <View style={styles.syncTitleRow}>
            <Text style={styles.syncTitle}>Background sync summary</Text>
            {lastSyncRelativeText ? <Text style={styles.syncMeta}>Updated {lastSyncRelativeText}</Text> : null}
          </View>
          <Text style={styles.syncSummary}>{lastBackgroundSyncSummary}</Text>
        </View>
      ) : null}

      {showQueueDetails && queueItems.length > 0 ? (
        <View style={styles.syncDetailCard}>
          <Text style={styles.syncDetailTitle}>Pending queue items</Text>
          {queueItems.map((item) => (
            <View key={item.id} style={styles.syncDetailRow}>
              <Text style={styles.syncDetailAction}>{item.action.type.replace(/_/g, " ")}</Text>
              <Text style={styles.syncDetailMeta}>
                Stop {item.action.stopId.slice(0, 8)} · attempts {item.attempts}
              </Text>
              <Text style={styles.syncDetailMeta}>
                Next retry: {new Date(item.retryAt).toLocaleTimeString()}
              </Text>
              {item.lastError ? (
                <Text style={styles.syncDetailError}>{item.lastError}</Text>
              ) : null}
            </View>
          ))}
          <Pressable
            style={[styles.secondaryButton, syncing && styles.buttonDisabled]}
            onPress={() => void runQueueSync(true)}
            disabled={syncing}
          >
            <Text style={styles.secondaryButtonText}>
              {syncing ? "Syncing..." : "Retry Sync Now"}
            </Text>
          </Pressable>
          <Pressable
            style={[styles.secondaryButton, styles.clearQueueButton, syncing && styles.buttonDisabled]}
            onPress={onClearQueue}
            disabled={syncing}
          >
            <Text style={styles.clearQueueButtonText}>Clear Queue</Text>
          </Pressable>
        </View>
      ) : null}

      {!visibleRoute && !loading ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyStateTitle}>No active route</Text>
          <Text style={styles.emptyStateHint}>Your dispatcher will assign a route when it is ready.</Text>
        </View>
      ) : null}

      {visibleRoute && routeStops.length === 0 && !loading ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyStateTitle}>No stops</Text>
          <Text style={styles.emptyStateHint}>This route has no stops assigned yet.</Text>
        </View>
      ) : null}

      {visibleRoute && allStopsTerminal ? (
        <View style={styles.completedState}>
          <Text style={styles.completedStateTitle}>All stops done!</Text>
          <Text style={styles.completedStateHint}>
            Great work. If route status has not updated yet, refresh once.
          </Text>
          {visibleRoute.status !== "COMPLETED" ? (
            <Pressable
              style={[styles.secondaryButton, refreshingCompletion && styles.buttonDisabled]}
              onPress={onRefreshRouteCompletion}
              disabled={refreshingCompletion}
            >
              <Text style={styles.secondaryButtonText}>
                {refreshingCompletion ? "Refreshing..." : "Refresh Route Status"}
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      {routeStops.map((stop) => {
        const isDepotStop = stop.stop_type === "DEPOT_RETURN" || stop.stop_type === "DEPOT_RELOAD";
        const isNextStop = visibleNextStop?.id === stop.id;
        const isDone = stop.status === "COMPLETED" || stop.status === "FAILED";
        const statusColors: Record<string, { bg: string; text: string }> = {
          PENDING:   { bg: "#e8f4fd", text: "#0369a1" },
          ARRIVED:   { bg: "#fef9c3", text: "#854d0e" },
          COMPLETED: { bg: "#dcfce7", text: "#166534" },
          FAILED:    { bg: "#fee2e2", text: "#991b1b" },
        };
        const badge = statusColors[stop.status] ?? statusColors.PENDING;
        const stopLabel = isDepotStop
          ? (stop.stop_type === "DEPOT_RETURN" ? "Return to Depot" : "Reload at Depot")
          : stop.stop_type.replace(/_/g, " ");

        return (
          <Pressable
            key={stop.id}
            style={[
              styles.stopCard,
              isDepotStop && styles.depotStopCard,
              isNextStop && styles.nextStopCard,
              isDone && styles.doneStopCard,
            ]}
            onPress={() => navigation.navigate("StopDetail", {
              routeId: visibleRoute!.id,
              stop,
              routeStatus: visibleRoute!.status,
            })}
          >
            {isNextStop && (
              <View style={styles.nextPill}>
                <Text style={styles.nextPillText}>NEXT</Text>
              </View>
            )}
            <View style={styles.stopHeader}>
              <View style={styles.stopHeaderLeft}>
                <View style={[styles.sequenceBadge, isNextStop && styles.sequenceBadgeNext, isDone && styles.sequenceBadgeDone]}>
                  <Text style={[styles.sequenceBadgeText, isDone && styles.sequenceBadgeTextDone]}>
                    {stop.sequence_number}
                  </Text>
                </View>
                <Text style={[styles.stopType, isDone && styles.dimText]}>{stopLabel}</Text>
              </View>
              <View style={[styles.stopBadge, { backgroundColor: badge.bg }]}>
                <Text style={[styles.stopBadgeText, { color: badge.text }]}>{stop.status.replace(/_/g, " ")}</Text>
              </View>
            </View>
            <Text style={[styles.stopAddress, isDone && styles.dimText]}>{stop.address}</Text>

            {isNextStop && !isDone && (
              <Pressable
                style={styles.navigateButton}
                onPress={() => {
                  const url = `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(stop.address)}`;
                  Linking.openURL(url).catch(() => {
                    Alert.alert("Cannot open maps", "No maps application found.");
                  });
                }}
              >
                <Text style={styles.navigateButtonText}>Navigate</Text>
              </Pressable>
            )}
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, backgroundColor: "#faf7f0" },
  fullscreenCenter: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#faf7f0",
    padding: 16,
  },
  title: { fontSize: 24, fontWeight: "700", color: "#1f1f1f" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 12 },
  statusCard: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    padding: 12,
    backgroundColor: "#fff",
    marginBottom: 10,
  },
  conflictCard: {
    borderWidth: 1,
    borderColor: "#f2b8a0",
    borderRadius: 12,
    padding: 12,
    backgroundColor: "#fff5f1",
    marginBottom: 12,
  },
  conflictTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: "#8f2d10",
    marginBottom: 4,
  },
  conflictHint: {
    color: "#6c665c",
    marginBottom: 6,
  },
  conflictItem: {
    color: "#7c2d12",
    marginBottom: 4,
    fontSize: 12,
  },
  errorContainer: {
    borderWidth: 1,
    borderColor: "#f2b8a0",
    borderRadius: 12,
    padding: 16,
    backgroundColor: "#fff5f1",
    marginTop: 12,
  },
  errorTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: "#8f2d10",
    marginBottom: 8,
  },
  errorText: {
    color: "#6c665c",
    marginBottom: 12,
  },
  retryButton: {
    alignSelf: "flex-start",
    backgroundColor: "#ff6f3d",
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  retryButtonText: {
    color: "#1b1b1b",
    fontWeight: "700",
  },
  syncCard: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    padding: 12,
    backgroundColor: "#fff",
    marginBottom: 12,
  },
  syncTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: "#1f1f1f",
    marginBottom: 6,
  },
  syncTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 6,
    gap: 8,
  },
  syncMeta: { color: "#6c665c", fontSize: 11 },
  syncSummary: {
    color: "#4a4a4a",
    fontSize: 12,
    lineHeight: 18,
  },
  statusTopRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 },
  statusLabel: { color: "#6c665c", marginBottom: 2, fontSize: 12 },
  statusValue: { fontSize: 16, fontWeight: "700", color: "#1f1f1f" },
  courierBadge: { borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4, backgroundColor: "#e2e8f0" },
  courierBadgeOnline: { backgroundColor: "#dcfce7" },
  courierBadgeBreak: { backgroundColor: "#fef9c3" },
  courierBadgeText: { fontSize: 12, fontWeight: "600", color: "#1f1f1f" },
  pushWarning: { backgroundColor: "#fff5f1", borderRadius: 8, padding: 8, marginTop: 6 },
  pushWarningText: { fontSize: 12, color: "#8f2d10" },
  syncBanner: { backgroundColor: "#eff6ff", borderRadius: 8, padding: 8, marginTop: 6 },
  syncBannerText: { fontSize: 12, color: "#1d4ed8" },
  syncDetailsButton: {
    alignSelf: "flex-start",
    marginTop: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: "#dbeafe",
  },
  syncDetailsButtonText: { color: "#1e40af", fontSize: 12, fontWeight: "600" },
  syncDetailCard: {
    borderWidth: 1,
    borderColor: "#bfdbfe",
    borderRadius: 12,
    padding: 12,
    backgroundColor: "#f8fbff",
    marginBottom: 12,
  },
  syncDetailTitle: { fontSize: 14, fontWeight: "700", color: "#1e3a8a", marginBottom: 8 },
  syncDetailRow: {
    borderWidth: 1,
    borderColor: "#dbeafe",
    borderRadius: 8,
    padding: 8,
    marginBottom: 8,
    backgroundColor: "#fff",
  },
  syncDetailAction: { fontWeight: "700", color: "#1f2937", fontSize: 12, marginBottom: 2 },
  syncDetailMeta: { color: "#4b5563", fontSize: 12 },
  syncDetailError: { color: "#9a3412", fontSize: 12, marginTop: 4 },
  progressBlock: { marginTop: 8 },
  progressRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  progressLabel: { color: "#6c665c", fontSize: 12 },
  progressValue: { color: "#1f1f1f", fontSize: 12, fontWeight: "600" },
  progressTrack: {
    height: 8,
    borderRadius: 999,
    backgroundColor: "#efe7db",
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: "#ff6f3d",
  },
  nextStopHint: { color: "#4a4a4a", fontSize: 13, marginTop: 8 },
  hint: { color: "#6c665c", marginBottom: 4 },
  actionRow: { flexDirection: "row", gap: 10, marginBottom: 12 },
  button: {
    flex: 1,
    backgroundColor: "#ff6f3d",
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: "center",
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: "#1b1b1b", fontWeight: "700" },
  statusButton: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: "center",
    backgroundColor: "#fff",
  },
  statusButtonActive: { backgroundColor: "#dcfce7", borderColor: "#86efac" },
  statusButtonBreak: { backgroundColor: "#fef9c3", borderColor: "#fde047" },
  statusButtonOffline: { backgroundColor: "#f1f5f9", borderColor: "#94a3b8" },
  statusButtonText: { fontWeight: "600", color: "#6c665c" },
  statusButtonTextActive: { color: "#1f1f1f" },
  secondaryButton: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: "#fff",
  },
  secondaryButtonText: { fontWeight: "600", color: "#1f1f1f" },
  clearQueueButton: {
    marginTop: 8,
    borderColor: "#fca5a5",
    backgroundColor: "#fff1f2",
  },
  clearQueueButtonText: {
    fontWeight: "700",
    color: "#b91c1c",
  },
  stopCard: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    padding: 12,
    backgroundColor: "#fff",
    marginBottom: 10,
  },
  nextStopCard: {
    borderWidth: 2,
    borderColor: "#ff6f3d",
    backgroundColor: "#fff9f6",
  },
  doneStopCard: {
    backgroundColor: "#f9f9f7",
    borderColor: "#e8e4de",
  },
  depotStopCard: {
    borderColor: "#0f7c90",
    backgroundColor: "#f0f9ff",
  },
  nextPill: {
    alignSelf: "flex-start",
    marginBottom: 8,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 3,
    backgroundColor: "#ff6f3d",
  },
  nextPillText: { color: "#fff", fontSize: 10, fontWeight: "800", letterSpacing: 0.6 },
  stopHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 },
  stopHeaderLeft: { flexDirection: "row", alignItems: "center", gap: 8, flex: 1, paddingRight: 8 },
  sequenceBadge: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: "#f3f4f6",
    borderWidth: 1,
    borderColor: "#d1d5db",
    alignItems: "center",
    justifyContent: "center",
  },
  sequenceBadgeNext: {
    backgroundColor: "#ff6f3d",
    borderColor: "#ff6f3d",
  },
  sequenceBadgeDone: {
    backgroundColor: "#e7e5e4",
    borderColor: "#d6d3d1",
  },
  sequenceBadgeText: { fontWeight: "700", color: "#1f1f1f", fontSize: 12 },
  sequenceBadgeTextDone: { color: "#78716c" },
  stopType: { fontWeight: "700", color: "#555", fontSize: 12, letterSpacing: 0.6, textTransform: "uppercase", flexShrink: 1 },
  stopBadge: { borderRadius: 6, paddingHorizontal: 7, paddingVertical: 2 },
  stopBadgeText: { fontSize: 11, fontWeight: "600" },
  stopAddress: { color: "#333", fontSize: 13 },
  dimText: { color: "#aaa" },
  navigateButton: {
    alignSelf: "flex-start",
    marginTop: 8,
    backgroundColor: "#ff6f3d",
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
  navigateButtonText: { color: "#1b1b1b", fontWeight: "700", fontSize: 13 },
  emptyState: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    padding: 20,
    alignItems: "center",
    backgroundColor: "#fff",
    marginTop: 8,
  },
  emptyStateTitle: { fontSize: 16, fontWeight: "600", color: "#1f1f1f", marginBottom: 4 },
  emptyStateHint: { color: "#8a8175", textAlign: "center", fontSize: 13 },
  completedState: {
    borderWidth: 1,
    borderColor: "#86efac",
    borderRadius: 12,
    padding: 20,
    alignItems: "center",
    backgroundColor: "#f0fdf4",
    marginBottom: 12,
  },
  completedStateTitle: { fontSize: 18, fontWeight: "700", color: "#166534", marginBottom: 4 },
  completedStateHint: { color: "#4ade80", textAlign: "center", fontSize: 13 },
});
