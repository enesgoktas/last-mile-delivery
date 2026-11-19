import { useEffect, useRef } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import type { BarcodeScanningResult } from "expo-camera";

type Props = {
  visible: boolean;
  /** Title shown at the top of the scanner modal. */
  title?: string;
  /** Optional one-line hint shown below the title. */
  hint?: string;
  /** Called once when a barcode is successfully scanned. Not called again until the modal is closed and re-opened. */
  onScan: (data: string) => void;
  onClose: () => void;
};

/**
 * Full-screen barcode scanner modal.  Resets its "already scanned" guard
 * every time the modal becomes visible so it can be used more than once.
 */
export function BarcodeScannerModal({
  visible,
  title = "Scan Barcode",
  hint,
  onScan,
  onClose,
}: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  // Prevent the onBarcodeScanned callback from firing multiple times for the
  // same scan session (the camera fires continuously while a barcode is in view).
  const scannedRef = useRef(false);

  useEffect(() => {
    if (visible) {
      scannedRef.current = false;
    }
  }, [visible]);

  const handleBarcodeScanned = (result: BarcodeScanningResult) => {
    if (scannedRef.current) return;
    scannedRef.current = true;
    onScan(result.data);
  };

  const handleDevPass = () => {
    if (scannedRef.current) return;
    scannedRef.current = true;
    onScan("DEV-PASS");
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      statusBarTranslucent
      onRequestClose={onClose}
    >
      <View style={styles.container}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>{title}</Text>
          {hint ? <Text style={styles.hint}>{hint}</Text> : null}
          {__DEV__ ? (
            <Pressable onPress={handleDevPass} style={styles.devPassButton}>
              <Text style={styles.devPassText}>Dev Pass</Text>
            </Pressable>
          ) : null}
        </View>

        {!permission ? (
          <View style={styles.center}>
            <Text style={styles.infoText}>Checking camera permission…</Text>
          </View>
        ) : !permission.granted ? (
          <View style={styles.center}>
            <Text style={styles.infoText}>
              Camera access is required to scan barcodes.
            </Text>
            <Pressable onPress={requestPermission} style={styles.grantButton}>
              <Text style={styles.grantText}>Grant Camera Permission</Text>
            </Pressable>
          </View>
        ) : (
          <View style={styles.cameraWrapper}>
            <CameraView
              style={styles.camera}
              barcodeScannerSettings={{
                barcodeTypes: [
                  "qr",
                  "code128",
                  "code39",
                  "code93",
                  "ean13",
                  "ean8",
                  "upc_a",
                  "upc_e",
                  "pdf417",
                  "datamatrix",
                ],
              }}
              onBarcodeScanned={handleBarcodeScanned}
            />
            {/* Targeting overlay */}
            <View style={styles.overlay} pointerEvents="none">
              <View style={styles.scanFrame} />
            </View>
          </View>
        )}

        {/* Cancel button always visible */}
        <View style={styles.footer}>
          <Pressable onPress={onClose} style={styles.cancelButton}>
            <Text style={styles.cancelText}>Cancel</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const FRAME_SIZE = 240;

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#000",
  },
  header: {
    paddingTop: 56,
    paddingBottom: 16,
    paddingHorizontal: 20,
    backgroundColor: "rgba(0,0,0,0.6)",
  },
  title: {
    fontSize: 18,
    fontWeight: "700",
    color: "#fff",
    textAlign: "center",
  },
  hint: {
    fontSize: 13,
    color: "rgba(255,255,255,0.7)",
    textAlign: "center",
    marginTop: 6,
  },
  devPassButton: {
    position: "absolute",
    right: 20,
    top: 56,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.5)",
    borderRadius: 6,
    paddingVertical: 4,
    paddingHorizontal: 8,
    backgroundColor: "rgba(0,0,0,0.35)",
  },
  devPassText: {
    color: "#fff",
    fontSize: 11,
    fontWeight: "600",
  },
  cameraWrapper: {
    flex: 1,
    position: "relative",
  },
  camera: {
    ...StyleSheet.absoluteFillObject,
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.4)",
  },
  scanFrame: {
    width: FRAME_SIZE,
    height: FRAME_SIZE,
    borderWidth: 2,
    borderColor: "#ff6f3d",
    borderRadius: 12,
    backgroundColor: "transparent",
  },
  center: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 32,
  },
  infoText: {
    color: "#fff",
    textAlign: "center",
    marginBottom: 20,
    lineHeight: 22,
  },
  grantButton: {
    backgroundColor: "#ff6f3d",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 24,
  },
  grantText: {
    color: "#fff",
    fontWeight: "700",
  },
  footer: {
    paddingVertical: 20,
    paddingHorizontal: 20,
    backgroundColor: "rgba(0,0,0,0.6)",
  },
  cancelButton: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.4)",
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
  },
  cancelText: {
    color: "#fff",
    fontWeight: "600",
    fontSize: 15,
  },
});
