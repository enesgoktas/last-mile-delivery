import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Image,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as ImagePicker from "expo-image-picker";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { arriveStop, completeStop, failStop, getOrderPackages } from "../services/api";
import { enqueueOfflineAction } from "../services/offlineQueue";
import { resolveStopCompletionPayload } from "../services/pod";
import { BarcodeScannerModal } from "../components/BarcodeScannerModal";
import { SignaturePadModal } from "../components/SignaturePadModal";
import {
  buildFailureReasonChoices,
  failureNoteRequired,
  getWaitTimeMinutes,
  requiresArrivalScan,
  requiresField,
  requiresPreArrivalCall,
  requiresReceiverNameForSafeDrop,
  sanitizeCompletionPayloadForStop,
  shouldQueueOfflineOnError,
  suggestsPreArrivalCall,
  toErrorMessage,
} from "./stopDetailHelpers";
import type { OrderPackage, RootStackParamList, StopStatus } from "../types";

type Props = NativeStackScreenProps<RootStackParamList, "StopDetail">;

// ── Status badge ─────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<StopStatus, { label: string; bg: string; fg: string }> = {
  PENDING:   { label: "Pending",   bg: "#e8f4fd", fg: "#0369a1" },
  ARRIVED:   { label: "Arrived",   bg: "#fef9c3", fg: "#854d0e" },
  COMPLETED: { label: "Completed", bg: "#dcfce7", fg: "#166534" },
  FAILED:    { label: "Failed",    bg: "#fee2e2", fg: "#991b1b" },
};

function StatusBadge({ status }: { status: StopStatus }) {
  const { label, bg, fg } = STATUS_BADGE[status] ?? STATUS_BADGE.PENDING;
  return (
    <View style={[styles.badge, { backgroundColor: bg }]}>
      <Text style={[styles.badgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export function StopDetailScreen({ route, navigation }: Props) {
  const scrollRef = useRef<ScrollView | null>(null);
  const { routeId, stop, routeStatus } = route.params;
  const stopId = stop.id;
  const actionPolicy = stop.action_policy;
  const proofPolicy = actionPolicy?.proof_of_delivery;
  const failureChoices = useMemo(
    () => buildFailureReasonChoices(actionPolicy),
    [actionPolicy],
  );

  // ── POD state ────────────────────────────────────────────────────────────
  const [proofNote, setProofNote] = useState("");
  const [failureNote, setFailureNote] = useState("");
  const [receiverName, setReceiverName] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [photoUris, setPhotoUris] = useState<string[]>([]);
  const [signatureUri, setSignatureUri] = useState<string | null>(null);
  const [selectedFailureKey, setSelectedFailureKey] = useState(
    failureChoices[0]?.key ?? "NOT_HOME",
  );
  const [working, setWorking] = useState(false);
  const [orderPackages, setOrderPackages] = useState<OrderPackage[]>([]);
  const [packagesLoading, setPackagesLoading] = useState(false);
  const [packagesError, setPackagesError] = useState<string | null>(null);

  // ── Modal visibility ─────────────────────────────────────────────────────
  const [showSignaturePad, setShowSignaturePad] = useState(false);
  const [showArrivalScan, setShowArrivalScan] = useState(false);
  const [showFailureForm, setShowFailureForm] = useState(false);

  // ── Policy enforcement state ──────────────────────────────────────────────
  // Mirrors the current stop status locally so we can update it after API calls
  // without needing to navigate away and re-enter.
  const [currentStatus, setCurrentStatus] = useState<StopStatus>(stop.status);
  // Tracks the time the courier arrived (set on successful arriveStop call).
  const [arrivalTime, setArrivalTime] = useState<number | null>(
    stop.actual_arrival ? new Date(stop.actual_arrival).getTime() : null,
  );
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  // Whether the driver has tapped the "Call Customer" button.
  const [callMade, setCallMade] = useState(false);
  // Whether the arrival scan has been completed.
  const [arrivalScanned, setArrivalScanned] = useState(false);

  const selectedFailureChoice =
    failureChoices.find((c) => c.key === selectedFailureKey) ?? failureChoices[0];

  const waitSeconds = stop.stop_type === "DELIVERY" ? getWaitTimeMinutes(actionPolicy) * 60 : 0;

  // ── Wait timer ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (currentStatus !== "ARRIVED" || waitSeconds === 0 || !arrivalTime) return;

    const tick = () => setElapsedSeconds(Math.floor((Date.now() - arrivalTime) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [currentStatus, arrivalTime, waitSeconds]);

  useEffect(() => {
    let cancelled = false;

    const loadPackages = async () => {
      if (!stop.order_id) {
        setOrderPackages([]);
        setPackagesError(null);
        setPackagesLoading(false);
        return;
      }
      setPackagesLoading(true);
      setPackagesError(null);
      try {
        const packages = await getOrderPackages(stop.order_id);
        if (!cancelled) setOrderPackages(packages);
      } catch (_error) {
        if (!cancelled) setPackagesError("Unable to load package details.");
      } finally {
        if (!cancelled) setPackagesLoading(false);
      }
    };

    void loadPackages();
    return () => {
      cancelled = true;
    };
  }, [stop.order_id]);

  const waitRemaining = Math.max(0, waitSeconds - elapsedSeconds);
  const waitElapsed = waitRemaining === 0;
  const routeIsActive = routeStatus == null || routeStatus === "ACTIVE";

  const onDevPassWait = () => {
    if (!__DEV__ || waitSeconds <= 0) return;
    const adjustedArrival = Date.now() - (waitSeconds + 1) * 1000;
    setArrivalTime(adjustedArrival);
    setElapsedSeconds(waitSeconds + 1);
  };

  // ── Derived gating booleans ───────────────────────────────────────────────
  const needsArrivalScan = requiresArrivalScan(actionPolicy);
  const needsCallBeforeArrival = requiresPreArrivalCall(actionPolicy);
  const callSuggested = suggestsPreArrivalCall(actionPolicy);
  const needsReceiverForSafeDrop = requiresReceiverNameForSafeDrop(actionPolicy);
  const isDeliveryStop = stop.stop_type === "DELIVERY";
  const isPickupStop = stop.stop_type === "PICKUP";
  const isChargingStop = stop.stop_type === "CHARGING";
  // No barcode-scan / pre-arrival-call gates for charging or depot stops.
  // The action policy is order-driven and doesn't apply when there's no
  // order at all, but we double-guard here so a misconfigured policy
  // can't keep the driver from completing a CHARGING stop.
  const requiresScanBeforeArrive = needsArrivalScan && !isPickupStop && !isChargingStop;
  const requiresScanBeforeComplete = needsArrivalScan && isPickupStop;
  // Solver-allocated charge duration in minutes (departure - arrival).
  // Falls back to the policy's wait time so the driver still sees *some*
  // expected dwell time even if the column is missing (legacy rows).
  const plannedChargeMinutes = isChargingStop
    ? (stop.planned_arrival && stop.planned_departure
        ? Math.max(
            0,
            Math.round(
              (new Date(stop.planned_departure).getTime()
                - new Date(stop.planned_arrival).getTime()) / 60000,
            ),
          )
        : null)
    : null;
  const requiredPhotoCount = isDeliveryStop && proofPolicy?.require_photo
    ? proofPolicy.photo_count
    : 0;
  const requiresSignature = isDeliveryStop && Boolean(proofPolicy?.require_signature);
  const requiresOtp = isDeliveryStop && Boolean(proofPolicy?.require_otp);

  // "Mark Arrived" requires: scan done (if policy) AND call made (if policy)
  const canArrive =
    (!requiresScanBeforeArrive || arrivalScanned) && (!needsCallBeforeArrival || callMade);

  // "Complete" requires: wait elapsed (if applicable) AND receiver name (if safe_drop blocked)
  const canComplete =
    waitElapsed &&
    (!requiresScanBeforeComplete || arrivalScanned) &&
    (!needsReceiverForSafeDrop || receiverName.trim().length > 0);
  const displayedPackages =
    stop.stop_type === "PICKUP" ? orderPackages.slice(0, 1) : orderPackages;

  // ── Photo capture ─────────────────────────────────────────────────────────
  const addPhotoUri = (uri: string) => {
    setPhotoUris((current) => {
      if (current.length >= 5) {
        Alert.alert("Photo limit reached", "You can attach up to 5 proof photos.");
        return current;
      }
      return [...current, uri];
    });
  };

  const openCamera = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (perm.status !== "granted") {
      Alert.alert("Permission required", "Allow camera access to take proof-of-delivery photos.");
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: false,
      quality: 0.8,
    });
    if (!result.canceled && result.assets[0]?.uri) {
      addPhotoUri(result.assets[0].uri);
    }
  };

  const openLibrary = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (perm.status !== "granted") {
      Alert.alert("Permission required", "Allow photo library access to attach proof-of-delivery files.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: false,
      quality: 0.8,
    });
    if (!result.canceled && result.assets[0]?.uri) {
      addPhotoUri(result.assets[0].uri);
    }
  };

  const onAddPhoto = () => {
    if (!isDeliveryStop) {
      return;
    }
    Alert.alert("Add Photo", "Choose how to add a proof photo", [
      { text: "Take Photo", onPress: openCamera },
      { text: "Choose from Library", onPress: openLibrary },
      { text: "Cancel", style: "cancel" },
    ]);
  };

  // ── Pre-arrival call ──────────────────────────────────────────────────────
  const onCallCustomer = () => {
    const phone = stop.customer_phone;
    if (!phone) {
      Alert.alert(
        "No phone on file",
        "There is no customer phone number attached to this stop. You may proceed.",
        [{ text: "OK", onPress: () => setCallMade(true) }],
      );
      return;
    }
    Linking.openURL(`tel:${phone}`).then(() => setCallMade(true)).catch(() => {
      Alert.alert("Cannot open dialer", "Your device does not support phone calls.");
      // Still mark the call as made so the driver is not blocked.
      setCallMade(true);
    });
  };

  // ── Arrive ────────────────────────────────────────────────────────────────
  const onArrive = async () => {
    if (!routeIsActive) {
      Alert.alert("Start route first", "You can only update stops after starting the route.");
      return;
    }
    setWorking(true);
    try {
      await arriveStop(routeId, stopId);
      const now = Date.now();
      setCurrentStatus("ARRIVED");
      setArrivalTime(now);
      Alert.alert("Arrived", "Stop marked as ARRIVED.");
    } catch (error) {
      if (shouldQueueOfflineOnError(error)) {
        await enqueueOfflineAction({ type: "ARRIVE_STOP", routeId, stopId });
        const now = Date.now();
        setCurrentStatus("ARRIVED");
        setArrivalTime(now);
        Alert.alert("Queued offline", "Arrival action saved and will be synced.");
      } else {
        Alert.alert("Unable to mark arrived", toErrorMessage(error));
      }
    } finally {
      setWorking(false);
    }
  };

  // ── Complete ──────────────────────────────────────────────────────────────
  const onComplete = async () => {
    if (!routeIsActive) {
      Alert.alert("Start route first", "You can only update stops after starting the route.");
      return;
    }
    if (isDeliveryStop && requiredPhotoCount > 0 && photoUris.length < requiredPhotoCount) {
      Alert.alert(
        "More proof needed",
        `Attach ${requiredPhotoCount} photo${requiredPhotoCount === 1 ? "" : "s"} before completing this stop.`,
      );
      return;
    }
    if (isDeliveryStop && requiresSignature && !signatureUri) {
      Alert.alert("Signature required", "Capture a signature before completing this stop.");
      return;
    }
    if (
      (requiresField(proofPolicy?.receiver_name_mode) || needsReceiverForSafeDrop) &&
      !receiverName.trim()
    ) {
      Alert.alert("Receiver name required", "Capture the receiver's name before completing this stop.");
      return;
    }
    if (isDeliveryStop && requiresOtp && !otpCode.trim()) {
      Alert.alert("OTP required", "Enter the delivery OTP before completing this stop.");
      return;
    }
    if (requiresField(proofPolicy?.notes_mode) && !proofNote.trim()) {
      Alert.alert("Note required", "Add a proof note before completing this stop.");
      return;
    }

    setWorking(true);
    const completionPayload = sanitizeCompletionPayloadForStop(stop.stop_type, {
      notes: proofNote || undefined,
      receiver_name: receiverName.trim() || undefined,
      otp_code: otpCode.trim() || undefined,
      photo_local_uris: photoUris.length ? photoUris : undefined,
      signature_local_uri: signatureUri || undefined,
    });
    try {
      const resolvedPayload = await resolveStopCompletionPayload(routeId, stopId, completionPayload);
      await completeStop(routeId, stopId, resolvedPayload);
      setCurrentStatus("COMPLETED");
      Alert.alert("Success", "Stop marked as completed.", [
        { text: "OK", onPress: () => navigation.goBack() },
      ]);
    } catch (error) {
      if (shouldQueueOfflineOnError(error)) {
        await enqueueOfflineAction({
          type: "COMPLETE_STOP",
          routeId,
          stopId,
          payload: completionPayload,
        });
        setCurrentStatus("COMPLETED");
        Alert.alert("Queued offline", "Completion was saved for automatic sync.", [
          { text: "OK", onPress: () => navigation.goBack() },
        ]);
      } else {
        Alert.alert("Unable to complete stop", toErrorMessage(error));
      }
    } finally {
      setWorking(false);
    }
  };

  // ── Fail ──────────────────────────────────────────────────────────────────
  const onFail = async () => {
    if (!routeIsActive) {
      Alert.alert("Start route first", "You can only update stops after starting the route.");
      return;
    }
    if (failureNoteRequired(selectedFailureChoice, actionPolicy) && !failureNote.trim()) {
      Alert.alert("Note required", "Add a note for the selected failure reason.");
      return;
    }
    setWorking(true);
    try {
      await failStop(
        routeId,
        stopId,
        selectedFailureChoice?.value ?? "OTHER",
        failureNote.trim() || undefined,
        selectedFailureChoice?.customReasonLabel ?? undefined,
      );
      setCurrentStatus("FAILED");
      Alert.alert("Saved", "Stop marked as failed.", [
        { text: "OK", onPress: () => navigation.goBack() },
      ]);
    } catch (error) {
      if (shouldQueueOfflineOnError(error)) {
        await enqueueOfflineAction({
          type: "FAIL_STOP",
          routeId,
          stopId,
          payload: {
            failure_reason: selectedFailureChoice?.value ?? "OTHER",
            notes: failureNote.trim() || undefined,
            custom_reason_label: selectedFailureChoice?.customReasonLabel ?? undefined,
          },
        });
        setCurrentStatus("FAILED");
        Alert.alert("Queued offline", "Failure action saved and will be synced.", [
          { text: "OK", onPress: () => navigation.goBack() },
        ]);
      } else {
        Alert.alert("Unable to fail stop", toErrorMessage(error));
      }
    } finally {
      setWorking(false);
    }
  };

  const toggleFailureForm = () => {
    if (!routeIsActive) {
      Alert.alert("Start route first", "You can only update stops after starting the route.");
      return;
    }
    setShowFailureForm((current) => {
      const next = !current;
      if (next) {
        setTimeout(() => {
          scrollRef.current?.scrollToEnd({ animated: true });
        }, 50);
      }
      return next;
    });
  };

  // ── Derived display ───────────────────────────────────────────────────────
  const isPending   = currentStatus === "PENDING";
  const isArrived   = currentStatus === "ARRIVED";
  const isTerminal  = currentStatus === "COMPLETED" || currentStatus === "FAILED";

  const formatWaitRemaining = (secs: number): string => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <>
      <ScrollView ref={scrollRef} contentContainerStyle={styles.container}>
        {/* Title + status badge */}
        <View style={styles.titleRow}>
          <Text style={styles.title}>Stop Detail</Text>
          <StatusBadge status={currentStatus} />
        </View>

        {/* Stop metadata */}
        <Text style={styles.address}>{stop.address}</Text>
        <Text style={styles.meta}>
          Stop #{stop.sequence_number} · {stop.stop_type}
        </Text>

        {/* Charging-stop primer. Replaces the missing order/POD sections
            so the driver knows what to do when the route lands on a
            solver-inserted EV charger. */}
        {isChargingStop ? (
          <View style={styles.chargingBox}>
            <Text style={styles.chargingTitle}>⚡ EV charging stop</Text>
            <Text style={styles.chargingBody}>
              Plug your vehicle in at this station before continuing. The
              dispatcher reserved this break in your route so you have
              range for the remaining stops.
            </Text>
            {plannedChargeMinutes != null && plannedChargeMinutes > 0 ? (
              <Text style={styles.chargingDuration}>
                Planned charge time: ~{plannedChargeMinutes} min
              </Text>
            ) : null}
            <Text style={styles.chargingHint}>
              Tap "Mark Arrived" when you plug in, and "Complete" once the
              vehicle reaches the planned state of charge.
            </Text>
          </View>
        ) : null}

        {/* Order package details */}
        {stop.order_id ? (
          <View style={styles.packageBox}>
            <Text style={styles.packageTitle}>Order packages</Text>
            {packagesLoading ? (
              <Text style={styles.packageMeta}>Loading packages…</Text>
            ) : packagesError ? (
              <Text style={styles.packageError}>{packagesError}</Text>
            ) : displayedPackages.length === 0 ? (
              <Text style={styles.packageMeta}>No package records for this order.</Text>
            ) : (
              displayedPackages.map((pkg) => (
                <View key={pkg.id} style={styles.packageRow}>
                  <Text style={styles.packageLabel}>{pkg.label}</Text>
                  <Text style={styles.packageMeta}>
                    {pkg.weight_kg.toFixed(1)} kg
                    {pkg.volume_cm3 ? ` · ${pkg.volume_cm3.toLocaleString()} cm³` : ""}
                  </Text>
                </View>
              ))
            )}
          </View>
        ) : null}

        {/* Policy summary (read-only display) */}
        {actionPolicy ? (
          <View style={styles.policyBox}>
            <Text style={styles.policyTitle}>Stop Rules</Text>
            <Text style={styles.policyText}>
              Arrival scan: {actionPolicy.require_arrival_scan ? "Required" : "Optional"}
            </Text>
            <Text style={styles.policyText}>
              Call before arrival: {String(actionPolicy.pre_arrival_call).replaceAll("_", " ")}
            </Text>
            <Text style={styles.policyText}>
              Wait time: {actionPolicy.wait_time_minutes} min
            </Text>
            <Text style={styles.policyText}>
              Safe drop: {String(actionPolicy.safe_drop_policy).replaceAll("_", " ")}
            </Text>
          </View>
        ) : null}

        {/* ── PENDING actions ─────────────────────────────────────────── */}
        {isPending && (
          <>
            {!routeIsActive ? (
              <Text style={styles.blockHint}>Start the route before updating this stop.</Text>
            ) : null}
            {/* Pre-arrival call */}
            {callSuggested && (
              <View style={styles.enforcementCard}>
                <Text style={styles.enforcementLabel}>
                  {needsCallBeforeArrival ? "Call required before arriving" : "Suggested: call customer first"}
                </Text>
                <Pressable
                  onPress={onCallCustomer}
                  style={[styles.callButton, callMade && styles.callButtonDone]}
                >
                  <Text style={[styles.callButtonText, callMade && styles.callButtonTextDone]}>
                    {callMade ? "✓ Called customer" : "Call Customer"}
                  </Text>
                </Pressable>
              </View>
            )}

            {/* Arrival scan gate */}
            {requiresScanBeforeArrive && (
              <View style={styles.enforcementCard}>
                <Text style={styles.enforcementLabel}>Scan package barcode to confirm arrival</Text>
                {arrivalScanned ? (
                  <View style={styles.scanDone}>
                    <Text style={styles.scanDoneText}>✓ Barcode scanned</Text>
                  </View>
                ) : (
                  <Pressable
                    onPress={() => setShowArrivalScan(true)}
                    style={styles.scanButton}
                  >
                    <Text style={styles.scanButtonText}>Scan Barcode</Text>
                  </Pressable>
                )}
              </View>
            )}

            <Pressable
              style={[styles.arriveButton, (!canArrive || !routeIsActive) && styles.buttonDisabled]}
              onPress={onArrive}
              disabled={working || !canArrive || !routeIsActive}
            >
              <Text style={styles.buttonText}>
                {working ? "Processing…" : "Mark Arrived"}
              </Text>
            </Pressable>
            {!routeIsActive ? null : !canArrive && (
              <Text style={styles.blockHint}>
                {!callMade && needsCallBeforeArrival
                  ? "Call the customer before arriving."
                  : "Complete required checks before arriving."}
              </Text>
            )}
          </>
        )}

        {/* ── ARRIVED actions ──────────────────────────────────────────── */}
        {isArrived && (
          <>
            {/* Wait time countdown */}
            {waitSeconds > 0 && (
              <View style={[styles.enforcementCard, waitElapsed && styles.enforcementCardGreen]}>
                {waitElapsed ? (
                  <Text style={[styles.enforcementLabel, styles.enforcementLabelGreen]}>
                    ✓ Wait time complete — you may proceed
                  </Text>
                ) : (
                  <>
                    <View style={styles.waitHeaderRow}>
                      <Text style={styles.enforcementLabel}>
                        Wait {getWaitTimeMinutes(actionPolicy)} min before completing
                      </Text>
                      {__DEV__ ? (
                        <Pressable style={styles.devPassWaitButton} onPress={onDevPassWait}>
                          <Text style={styles.devPassWaitButtonText}>Dev Pass</Text>
                        </Pressable>
                      ) : null}
                    </View>
                    <Text style={styles.timer}>{formatWaitRemaining(waitRemaining)}</Text>
                  </>
                )}
              </View>
            )}

            {requiresScanBeforeComplete && (
              <View style={styles.enforcementCard}>
                <Text style={styles.enforcementLabel}>Scan package barcode before completing</Text>
                {arrivalScanned ? (
                  <View style={styles.scanDone}>
                    <Text style={styles.scanDoneText}>✓ Barcode scanned</Text>
                  </View>
                ) : (
                  <Pressable
                    onPress={() => setShowArrivalScan(true)}
                    style={styles.scanButton}
                  >
                    <Text style={styles.scanButtonText}>Scan Barcode</Text>
                  </Pressable>
                )}
              </View>
            )}

            {isDeliveryStop ? (
              <View style={styles.podSection}>
                <Text style={styles.sectionTitle}>Proof of delivery</Text>
                {proofPolicy ? (
                  <Text style={styles.helperText}>
                    {requiredPhotoCount > 0 ? `${requiredPhotoCount} photo(s) required` : "Photos optional"}
                    {requiresSignature ? " · Signature required" : " · Signature optional"}
                    {requiresOtp ? " · OTP required" : ""}
                    {needsReceiverForSafeDrop ? " · Receiver name required (safe drop blocked)" : ""}
                  </Text>
                ) : (
                  <Text style={styles.helperText}>No special proof requirements on this stop.</Text>
                )}

                {(proofPolicy && proofPolicy.receiver_name_mode !== "OFF") || needsReceiverForSafeDrop ? (
                  <TextInput
                    style={styles.singleLineInput}
                    value={receiverName}
                    onChangeText={setReceiverName}
                    placeholder={
                      (requiresField(proofPolicy?.receiver_name_mode) || needsReceiverForSafeDrop)
                        ? "Receiver name (required)"
                        : "Receiver name"
                    }
                  />
                ) : null}

                {requiresOtp ? (
                  <TextInput
                    style={styles.singleLineInput}
                    value={otpCode}
                    onChangeText={setOtpCode}
                    placeholder="Delivery OTP"
                    keyboardType="number-pad"
                  />
                ) : null}

                <TextInput
                  style={styles.input}
                  value={proofNote}
                  onChangeText={setProofNote}
                  placeholder={
                    requiresField(proofPolicy?.notes_mode) ? "Proof note (required)" : "Proof note"
                  }
                  multiline
                />

                <View style={styles.podRow}>
                  <Pressable
                    style={styles.secondaryAction}
                    onPress={onAddPhoto}
                    disabled={working}
                  >
                    <Text style={styles.secondaryActionText}>
                      Add Photo {requiredPhotoCount > 0 ? `(${photoUris.length}/${requiredPhotoCount})` : ""}
                    </Text>
                  </Pressable>
                </View>

                {photoUris.length > 0 ? (
                  <View style={styles.fileList}>
                    {photoUris.map((uri, index) => (
                      <View key={`${uri}-${index}`} style={styles.fileRow}>
                        <Image
                          source={{ uri }}
                          style={styles.photoThumb}
                          resizeMode="cover"
                        />
                        <Text style={styles.fileText} numberOfLines={1}>
                          Photo {index + 1}
                        </Text>
                        <Pressable
                          onPress={() =>
                            setPhotoUris((current) =>
                              current.filter((_, i) => i !== index),
                            )
                          }
                        >
                          <Text style={styles.clearActionText}>Remove</Text>
                        </Pressable>
                      </View>
                    ))}
                  </View>
                ) : (
                  <Text style={styles.podMeta}>No photos attached.</Text>
                )}

                <View style={styles.podRow}>
                  <Pressable
                    style={styles.secondaryAction}
                    onPress={() => setShowSignaturePad(true)}
                    disabled={working}
                  >
                    <Text style={styles.secondaryActionText}>
                      {signatureUri ? "Replace Signature" : "Capture Signature"}
                    </Text>
                  </Pressable>
                  {signatureUri ? (
                    <Pressable
                      style={styles.clearAction}
                      onPress={() => setSignatureUri(null)}
                      disabled={working}
                    >
                      <Text style={styles.clearActionText}>Clear</Text>
                    </Pressable>
                  ) : null}
                </View>
                {signatureUri ? (
                  <View style={styles.signaturePreviewBox}>
                    <Image
                      source={{ uri: signatureUri }}
                      style={styles.signaturePreview}
                      resizeMode="contain"
                    />
                    <Text style={styles.podMeta}>Signature captured.</Text>
                  </View>
                ) : (
                  <Text style={styles.podMeta}>No signature captured.</Text>
                )}
              </View>
            ) : null}

            {/* Action buttons */}
            <Pressable
              style={[styles.completeButton, (!canComplete || !routeIsActive) && styles.buttonDisabled]}
              onPress={onComplete}
              disabled={working || !canComplete || !routeIsActive}
            >
              <Text style={styles.buttonText}>Complete Stop</Text>
            </Pressable>
            {!routeIsActive ? (
              <Text style={styles.blockHint}>Start the route before updating this stop.</Text>
            ) : !canComplete ? (
              <Text style={styles.blockHint}>
                {!waitElapsed && waitSeconds > 0
                  ? `Wait ${formatWaitRemaining(waitRemaining)} before completing.`
                  : requiresScanBeforeComplete && !arrivalScanned
                    ? "Scan the package barcode before completing."
                    : "Complete required checks before finishing this stop."}
              </Text>
            ) : null}

            <Pressable
              style={[styles.failToggleButton, !routeIsActive && styles.buttonDisabled]}
              onPress={toggleFailureForm}
              disabled={working || !routeIsActive}
            >
              <Text style={styles.failToggleButtonText}>
                {showFailureForm ? "Hide Fail Stop" : "Fail Stop"}
              </Text>
            </Pressable>

            {showFailureForm ? (
              <View style={styles.failureFormCard}>
                <Text style={styles.sectionTitle}>Failure reason</Text>
                <View style={styles.reasonGrid}>
                  {failureChoices.map((choice) => (
                    <Pressable
                      key={choice.key}
                      style={[
                        styles.reasonChip,
                        selectedFailureKey === choice.key && styles.reasonChipSelected,
                      ]}
                      onPress={() => setSelectedFailureKey(choice.key)}
                    >
                      <Text style={styles.reasonText}>{choice.label}</Text>
                    </Pressable>
                  ))}
                </View>

                <Text style={styles.sectionTitle}>Failure note</Text>
                <TextInput
                  style={styles.input}
                  value={failureNote}
                  onChangeText={setFailureNote}
                  placeholder="Why did this stop fail?"
                  multiline
                />

                <Pressable
                  style={styles.failButton}
                  onPress={onFail}
                  disabled={working}
                >
                  <Text style={styles.buttonText}>Confirm Fail Stop</Text>
                </Pressable>
              </View>
            ) : null}
          </>
        )}

        {/* ── TERMINAL state (read-only) ────────────────────────────── */}
        {isTerminal && (
          <View style={styles.terminalCard}>
            <Text style={styles.terminalText}>
              This stop has been {currentStatus === "COMPLETED" ? "completed" : "marked as failed"}.
            </Text>
            <Pressable style={styles.backButton} onPress={() => navigation.goBack()}>
              <Text style={styles.backButtonText}>Back to Route</Text>
            </Pressable>
          </View>
        )}
      </ScrollView>

      {/* Modals */}
      <SignaturePadModal
        visible={showSignaturePad}
        onSave={(sig) => {
          setSignatureUri(sig);
          setShowSignaturePad(false);
        }}
        onClose={() => setShowSignaturePad(false)}
      />

      <BarcodeScannerModal
        visible={showArrivalScan}
        title="Scan Package Barcode"
        hint="Position the barcode within the frame to confirm arrival."
        onScan={(data) => {
          setArrivalScanned(true);
          setShowArrivalScan(false);
          Alert.alert("Scanned", `Package barcode confirmed: ${data.slice(0, 20)}${data.length > 20 ? "…" : ""}`);
        }}
        onClose={() => setShowArrivalScan(false)}
      />
    </>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { padding: 16, backgroundColor: "#faf7f0" },

  // Header
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  title: { fontSize: 22, fontWeight: "700", color: "#1f1f1f" },
  badge: {
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  badgeText: { fontSize: 12, fontWeight: "700" },

  // Stop meta
  address: { color: "#1f1f1f", fontSize: 15, fontWeight: "600", marginBottom: 4 },
  meta: { color: "#6c665c", marginBottom: 12, fontSize: 13 },
  packageBox: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e8dccb",
    borderRadius: 10,
    padding: 12,
    marginBottom: 14,
    gap: 8,
  },
  packageTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: "#44403c",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  packageRow: {
    borderTopWidth: 1,
    borderTopColor: "#f0ebe2",
    paddingTop: 8,
  },
  packageLabel: { color: "#1f1f1f", fontWeight: "600", fontSize: 13 },
  packageMeta: { color: "#6c665c", fontSize: 12, marginTop: 2 },
  packageError: { color: "#9a3412", fontSize: 12 },

  // Dispatcher note
  dispatcherNoteBox: {
    backgroundColor: "#eef6ff",
    borderWidth: 1,
    borderColor: "#b3d4fc",
    borderRadius: 10,
    padding: 12,
    marginBottom: 14,
  },
  dispatcherNoteLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: "#2563eb",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  dispatcherNoteText: { fontSize: 14, color: "#1e3a5f", lineHeight: 20 },

  // Policy box
  policyBox: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e8dccb",
    borderRadius: 10,
    padding: 12,
    marginBottom: 14,
    gap: 4,
  },
  policyTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: "#44403c",
    textTransform: "uppercase",
    marginBottom: 2,
  },
  policyText: { color: "#57534e", fontSize: 13 },

  // Charging-stop primer
  chargingBox: {
    backgroundColor: "#ecfdf5",
    borderWidth: 1,
    borderColor: "#a7f3d0",
    borderRadius: 10,
    padding: 14,
    marginBottom: 14,
    gap: 6,
  },
  chargingTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: "#065f46",
  },
  chargingBody: { color: "#065f46", fontSize: 13, lineHeight: 19 },
  chargingDuration: {
    color: "#047857",
    fontWeight: "700",
    fontSize: 13,
    marginTop: 4,
  },
  chargingHint: { color: "#065f46", fontSize: 12, marginTop: 4, lineHeight: 17 },

  // Enforcement cards
  enforcementCard: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e8dccb",
    borderRadius: 10,
    padding: 12,
    marginBottom: 10,
  },
  enforcementCardGreen: {
    borderColor: "#86efac",
    backgroundColor: "#f0fdf4",
  },
  enforcementLabel: {
    fontSize: 13,
    fontWeight: "600",
    color: "#44403c",
    marginBottom: 8,
  },
  enforcementLabelGreen: { color: "#166534" },
  waitHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  devPassWaitButton: {
    borderWidth: 1,
    borderColor: "#b8b2a8",
    borderRadius: 6,
    paddingVertical: 2,
    paddingHorizontal: 8,
    backgroundColor: "#f7f3ec",
  },
  devPassWaitButtonText: {
    color: "#555",
    fontSize: 10,
    fontWeight: "600",
  },
  callButton: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#0f7c90",
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
  },
  callButtonDone: {
    borderColor: "#2f9e44",
    backgroundColor: "#f0fdf4",
  },
  callButtonText: { color: "#0f7c90", fontWeight: "600" },
  callButtonTextDone: { color: "#166534" },
  scanButton: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#6366f1",
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
  },
  scanButtonText: { color: "#4f46e5", fontWeight: "600" },
  scanDone: {
    backgroundColor: "#f0fdf4",
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
  },
  scanDoneText: { color: "#166534", fontWeight: "600" },
  timer: {
    fontSize: 28,
    fontWeight: "700",
    color: "#b45309",
    textAlign: "center",
    letterSpacing: 2,
  },

  // Section
  sectionTitle: { fontWeight: "600", marginBottom: 8, color: "#333", marginTop: 4 },
  helperText: { color: "#6c665c", fontSize: 12, marginBottom: 8 },
  blockHint: {
    color: "#9a3412",
    fontSize: 12,
    textAlign: "center",
    marginBottom: 8,
    marginTop: -4,
  },

  // Failure reason chips
  reasonGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  reasonChip: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 999,
    backgroundColor: "#fff",
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  reasonChipSelected: { backgroundColor: "#ffe6dd", borderColor: "#ff6f3d" },
  reasonText: { fontSize: 12, fontWeight: "600", color: "#222" },

  // Inputs
  input: {
    minHeight: 90,
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 10,
    backgroundColor: "#fff",
    padding: 10,
    marginBottom: 12,
    textAlignVertical: "top",
  },
  singleLineInput: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 10,
    backgroundColor: "#fff",
    padding: 10,
    marginBottom: 10,
  },

  // POD section
  podSection: { marginBottom: 12 },
  podRow: {
    flexDirection: "row",
    gap: 8,
    alignItems: "center",
    marginBottom: 6,
  },
  secondaryAction: {
    backgroundColor: "#fff",
    borderColor: "#d4c8b7",
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 10,
  },
  secondaryActionText: { color: "#1f1f1f", fontWeight: "600" },
  clearAction: {
    backgroundColor: "#fff",
    borderColor: "#e9b9a6",
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 10,
  },
  clearActionText: { color: "#9c3f1f", fontWeight: "600" },
  podMeta: { color: "#6c665c", fontSize: 12, marginBottom: 8 },

  // Photo thumbnails
  fileList: { gap: 6, marginBottom: 8 },
  fileRow: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#ece3d5",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  photoThumb: {
    width: 48,
    height: 48,
    borderRadius: 6,
    backgroundColor: "#f5f5f0",
  },
  fileText: { flex: 1, color: "#44403c", fontSize: 12 },

  // Signature preview
  signaturePreviewBox: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e8dccb",
    borderRadius: 8,
    padding: 8,
    marginBottom: 8,
  },
  signaturePreview: {
    width: "100%",
    height: 80,
  },

  // Action buttons
  arriveButton: {
    backgroundColor: "#0f7c90",
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
    marginBottom: 10,
  },
  completeButton: {
    backgroundColor: "#2f9e44",
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
    marginBottom: 10,
  },
  failButton: {
    backgroundColor: "#d9480f",
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
    marginBottom: 12,
  },
  failToggleButton: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#d9480f",
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
    marginBottom: 12,
  },
  failToggleButtonText: { color: "#9a3412", fontWeight: "700" },
  failureFormCard: {
    borderWidth: 1,
    borderColor: "#fecaca",
    borderRadius: 10,
    padding: 12,
    backgroundColor: "#fff7f7",
    marginBottom: 12,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: "#fff", fontWeight: "700" },

  // Terminal state
  terminalCard: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e8dccb",
    borderRadius: 12,
    padding: 20,
    alignItems: "center",
    marginTop: 12,
  },
  terminalText: {
    color: "#44403c",
    fontSize: 15,
    fontWeight: "600",
    textAlign: "center",
    marginBottom: 16,
  },
  backButton: {
    backgroundColor: "#1f1f1f",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 24,
  },
  backButtonText: { color: "#fff", fontWeight: "700" },
});
