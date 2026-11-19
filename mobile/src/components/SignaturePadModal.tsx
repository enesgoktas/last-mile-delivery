import { useRef } from "react";
import { Alert, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import SignatureCanvas, { SignatureViewRef } from "react-native-signature-canvas";

type Props = {
  visible: boolean;
  onSave: (base64DataUri: string) => void;
  onClose: () => void;
};

/**
 * Full-screen modal that lets the courier draw a recipient signature.
 * The `onSave` callback receives a base64 PNG data URI
 * (e.g. "data:image/png;base64,...") which the POD upload pipeline
 * handles via `dataUriToBlob` without needing a temp file.
 */
export function SignaturePadModal({ visible, onSave, onClose }: Props) {
  const ref = useRef<SignatureViewRef>(null);

  const handleSave = () => {
    ref.current?.readSignature();
  };

  const handleClear = () => {
    ref.current?.clearSignature();
  };

  const handleEmpty = () => {
    Alert.alert("Empty signature", "Please draw a signature before saving.");
  };

  // Hide the canvas's own built-in buttons so we render our own in native UI.
  const webStyle = `
    .m-signature-pad--footer { display: none; }
    .m-signature-pad { border: none; box-shadow: none; }
    .m-signature-pad--body { border: 2px dashed #d4c8b7; border-radius: 8px; }
  `;

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="formSheet"
      onRequestClose={onClose}
    >
      <View style={styles.container}>
        {/* Header */}
        <View style={styles.header}>
          <Pressable onPress={onClose} style={styles.headerAction}>
            <Text style={styles.cancelText}>Cancel</Text>
          </Pressable>
          <Text style={styles.title}>Recipient Signature</Text>
          <Pressable onPress={handleSave} style={styles.headerAction}>
            <Text style={styles.saveText}>Save</Text>
          </Pressable>
        </View>

        <Text style={styles.hint}>Have the recipient sign in the box below.</Text>

        {/* Signature canvas occupies remaining space */}
        <View style={styles.canvasWrapper}>
          <SignatureCanvas
            ref={ref}
            onOK={onSave}
            onEmpty={handleEmpty}
            webStyle={webStyle}
            autoClear={false}
            descriptionText=""
          />
        </View>

        {/* Clear button */}
        <View style={styles.footer}>
          <Pressable onPress={handleClear} style={styles.clearButton}>
            <Text style={styles.clearText}>Clear Signature</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#faf7f0",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: "#e8dccb",
    backgroundColor: "#fff",
  },
  headerAction: {
    minWidth: 56,
  },
  title: {
    fontSize: 16,
    fontWeight: "700",
    color: "#1f1f1f",
    textAlign: "center",
  },
  cancelText: {
    color: "#6c665c",
    fontSize: 15,
  },
  saveText: {
    color: "#ff6f3d",
    fontSize: 15,
    fontWeight: "700",
    textAlign: "right",
  },
  hint: {
    textAlign: "center",
    color: "#6c665c",
    fontSize: 13,
    paddingVertical: 10,
  },
  canvasWrapper: {
    flex: 1,
    marginHorizontal: 16,
    marginBottom: 8,
    borderRadius: 10,
    overflow: "hidden",
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e8dccb",
  },
  footer: {
    padding: 16,
    borderTopWidth: 1,
    borderTopColor: "#e8dccb",
    backgroundColor: "#fff",
  },
  clearButton: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  clearText: {
    color: "#44403c",
    fontWeight: "600",
  },
});
