import { useCallback, useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { getActiveRoute, startRoute, getCourierCurrentLoad, getMyCourierProfile } from "../services/api";
import { BarcodeScannerModal } from "../components/BarcodeScannerModal";
import type { RootStackParamList, PackageItem } from "../types";
import { buildRouteStartFailureMessage } from "./loadingChecklistHelpers";

type Props = NativeStackScreenProps<RootStackParamList, "LoadingChecklist">;

export function LoadingChecklistScreen({ route, navigation }: Props) {
  const { routeId } = route.params;
  const [packages, setPackages] = useState<PackageItem[]>([]);
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [scanTargetId, setScanTargetId] = useState<string | null>(null);

  const loadPackages = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const profile = await getMyCourierProfile();
      const loadData = await getCourierCurrentLoad(profile.id);
      setPackages(loadData.packages);
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Failed to load package list. Check your connection and try again.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPackages();
  }, [loadPackages]);

  const handleBarcodeScan = (data: string) => {
    const targetPackage = scanTargetId
      ? packages.find((pkg) => pkg.package_id === scanTargetId)
      : null;
    setScanTargetId(null);
    if (data === "DEV-PASS") {
      if (targetPackage) {
        setConfirmed((prev) => new Set([...prev, targetPackage.package_id]));
        Alert.alert("Package confirmed", `${targetPackage.label} has been marked as loaded (dev pass).`);
      } else {
        Alert.alert("No package selected", "Select a package and scan its barcode.");
      }
      return;
    }
    if (!targetPackage) {
      Alert.alert("No package selected", "Select a package and scan its barcode.");
      return;
    }
    const isMatch = targetPackage.package_id === data || targetPackage.label.toLowerCase() === data.toLowerCase();
    if (isMatch) {
      setConfirmed((prev) => new Set([...prev, targetPackage.package_id]));
      Alert.alert("Package confirmed", `${targetPackage.label} has been marked as loaded.`);
    } else {
      Alert.alert(
        "Wrong package",
        `Scanned: "${data.slice(0, 40)}"\n\nThis barcode does not match ${targetPackage.label}.`,
      );
    }
  };

  const allConfirmed = packages.length === 0 || confirmed.size >= packages.length;
  const confirmedPercent = packages.length > 0
    ? Math.min(100, Math.round((confirmed.size / packages.length) * 100))
    : 100;

  const onStartRoute = async () => {
    if (!allConfirmed) {
      Alert.alert("Confirm all packages", "Please confirm all packages are loaded before starting the route.");
      return;
    }
    setStarting(true);
    try {
      await startRoute(routeId);
      Alert.alert("Route started", "Route is now ACTIVE.");
      navigation.goBack();
    } catch (error) {
      Alert.alert("Unable to start route", buildRouteStartFailureMessage(error));
    } finally {
      setStarting(false);
    }
  };

  return (
    <>
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Loading Checklist</Text>
        <Text style={styles.subtitle}>
          Confirm each package is loaded onto your vehicle before starting the route.
        </Text>

        {loading ? (
          <Text style={styles.hint}>Loading packages…</Text>
        ) : loadError ? (
          // 1.8 — Error state with retry: the driver must not bypass this screen.
          <View style={styles.errorCard}>
            <Text style={styles.errorTitle}>Could not load package list</Text>
            <Text style={styles.errorDetail}>{loadError}</Text>
            <Pressable onPress={loadPackages} style={styles.retryButton}>
              <Text style={styles.retryText}>Try Again</Text>
            </Pressable>
            <Text style={styles.errorHint}>
              You cannot start the route until the package list loads successfully.
            </Text>
          </View>
        ) : packages.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyText}>No packages to confirm. You can start the route.</Text>
          </View>
        ) : (
          <>
            <View style={styles.progressCard}>
              <View style={styles.progressRow}>
                <Text style={styles.progressLabel}>Load Progress</Text>
                <Text style={styles.progressValue}>
                  {confirmed.size}/{packages.length} confirmed
                </Text>
              </View>
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: `${confirmedPercent}%` }]} />
              </View>
            </View>

            {packages.map((pkg) => (
              <View
                key={pkg.package_id}
                style={[styles.packageCard, confirmed.has(pkg.package_id) && styles.packageCardConfirmed]}
              >
                <View style={styles.packageHeader}>
                  <View style={[styles.checkbox, confirmed.has(pkg.package_id) && styles.checkboxChecked]}>
                    {confirmed.has(pkg.package_id) ? <Text style={styles.checkmark}>✓</Text> : null}
                  </View>
                  <Text style={styles.packageLabel}>{pkg.label}</Text>
                </View>
                <Text style={styles.packageMeta}>
                  {pkg.weight_kg} kg{pkg.volume_cm3 ? ` · ${pkg.volume_cm3} cm³` : ""}
                </Text>
                {!confirmed.has(pkg.package_id) ? (
                  <Pressable
                    style={styles.scanPackageButton}
                    onPress={() => setScanTargetId(pkg.package_id)}
                  >
                    <Text style={styles.scanPackageButtonText}>Scan Barcode</Text>
                  </Pressable>
                ) : null}
              </View>
            ))}
          </>
        )}

        <Pressable
          style={[
            styles.startButton,
            (!allConfirmed || !!loadError) && styles.buttonDisabled,
          ]}
          onPress={onStartRoute}
          disabled={starting || !allConfirmed || !!loadError}
        >
          <Text style={styles.startButtonText}>
            {starting
              ? "Starting…"
              : loadError
              ? "Cannot start — package list failed to load"
              : `Start Route (${confirmed.size}/${packages.length} confirmed)`}
          </Text>
        </Pressable>
      </ScrollView>

      {/* Barcode scanner modal */}
      <BarcodeScannerModal
        visible={scanTargetId !== null}
        title={scanTargetId ? "Scan Selected Package" : "Scan Package Barcode"}
        hint={
          scanTargetId
            ? "Scan selected package barcode. Dev Pass confirms selected package."
            : "Scan a barcode on a package to confirm it's loaded."
        }
        onScan={handleBarcodeScan}
        onClose={() => setScanTargetId(null)}
      />
    </>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, backgroundColor: "#faf7f0" },
  title: { fontSize: 24, fontWeight: "700", color: "#1f1f1f", marginBottom: 4 },
  subtitle: { color: "#6c665c", marginBottom: 16, lineHeight: 20 },
  hint: { color: "#6c665c", marginBottom: 12 },

  // Error state
  errorCard: {
    borderWidth: 1,
    borderColor: "#fca5a5",
    borderRadius: 12,
    padding: 16,
    backgroundColor: "#fff5f5",
    marginBottom: 16,
    gap: 8,
  },
  errorTitle: {
    fontWeight: "700",
    color: "#991b1b",
    fontSize: 15,
  },
  errorDetail: {
    color: "#7f1d1d",
    fontSize: 13,
    lineHeight: 18,
  },
  retryButton: {
    backgroundColor: "#dc2626",
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
  },
  retryText: { color: "#fff", fontWeight: "700" },
  errorHint: {
    color: "#991b1b",
    fontSize: 12,
    fontStyle: "italic",
    textAlign: "center",
  },

  // Empty state
  emptyCard: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    padding: 16,
    backgroundColor: "#fff",
    marginBottom: 16,
  },
  emptyText: { color: "#6c665c", textAlign: "center" },

  // Progress row
  progressCard: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    backgroundColor: "#fff",
    padding: 12,
    marginBottom: 12,
  },
  progressRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  progressLabel: { color: "#6c665c", fontSize: 12 },
  progressValue: { color: "#1f1f1f", fontWeight: "700", fontSize: 12 },
  progressTrack: {
    height: 8,
    borderRadius: 999,
    backgroundColor: "#efe7db",
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    backgroundColor: "#2f9e44",
  },
  // Package cards
  packageCard: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 12,
    padding: 12,
    backgroundColor: "#fff",
    marginBottom: 8,
  },
  packageCardConfirmed: {
    borderColor: "#2f9e44",
    backgroundColor: "#f0fdf4",
  },
  packageHeader: { flexDirection: "row", alignItems: "center", marginBottom: 4 },
  checkbox: {
    width: 22,
    height: 22,
    borderRadius: 4,
    borderWidth: 2,
    borderColor: "#d4c8b7",
    marginRight: 10,
    justifyContent: "center",
    alignItems: "center",
  },
  checkboxChecked: {
    borderColor: "#2f9e44",
    backgroundColor: "#2f9e44",
  },
  checkmark: { color: "#fff", fontSize: 14, fontWeight: "700" },
  packageLabel: { fontWeight: "700", fontSize: 15, color: "#1f1f1f" },
  packageMeta: { color: "#6c665c", marginLeft: 32 },
  scanPackageButton: {
    marginTop: 10,
    marginLeft: 32,
    borderWidth: 1,
    borderColor: "#6366f1",
    borderRadius: 8,
    backgroundColor: "#fff",
    paddingVertical: 8,
    alignItems: "center",
  },
  scanPackageButtonText: {
    color: "#4f46e5",
    fontWeight: "700",
    fontSize: 12,
  },

  // Start button
  startButton: {
    backgroundColor: "#2f9e44",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 16,
  },
  buttonDisabled: { opacity: 0.5 },
  startButtonText: { color: "#fff", fontWeight: "700", fontSize: 16 },
});
