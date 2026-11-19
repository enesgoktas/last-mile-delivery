import { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { login } from "../services/api";
import type { RootStackParamList } from "../types";
import { getLoginFailureMessage, validateLoginInput } from "./loginHelpers";

type Props = NativeStackScreenProps<RootStackParamList, "Login">;

export function LoginScreen({ navigation }: Props) {
  const [tenantSlug, setTenantSlug] = useState(__DEV__ ? "default" : "");
  const [email, setEmail] = useState(__DEV__ ? "courier.moto@delivery.com" : "");
  const [password, setPassword] = useState(__DEV__ ? "courier123" : "");
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const inputValidationError = validateLoginInput(tenantSlug, email, password);
  const canSubmit = !loading && inputValidationError === null;

  const onLogin = async () => {
    setFormError(null);
    if (inputValidationError) {
      setFormError(inputValidationError);
      return;
    }

    setLoading(true);
    try {
      await login(email.trim().toLowerCase(), password, tenantSlug.trim());
      navigation.replace("MyRoute");
    } catch (error) {
      setFormError(getLoginFailureMessage(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.keyboardView}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
      >
        {/* Branding */}
        <View style={styles.brandRow}>
          <View style={styles.logoBox}>
            <Text style={styles.logoText}>LM</Text>
          </View>
          <View>
            <Text style={styles.appName}>LastMile</Text>
            <Text style={styles.appTagline}>Courier App</Text>
          </View>
        </View>

        <Text style={styles.title}>Sign in to your account</Text>

        {formError ? (
          <View style={styles.validationError}>
            <Text style={styles.validationErrorText}>{formError}</Text>
          </View>
        ) : null}

        <TextInput
          style={styles.input}
          value={tenantSlug}
          onChangeText={(value) => {
            setTenantSlug(value);
            if (formError) setFormError(null);
          }}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="Tenant"
          placeholderTextColor="#b0a898"
          textContentType="organizationName"
          returnKeyType="next"
        />
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={(value) => {
            setEmail(value);
            if (formError) setFormError(null);
          }}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          placeholder="Email"
          placeholderTextColor="#b0a898"
          textContentType="emailAddress"
          returnKeyType="next"
        />
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={(value) => {
            setPassword(value);
            if (formError) setFormError(null);
          }}
          secureTextEntry
          placeholder="Password"
          placeholderTextColor="#b0a898"
          textContentType="password"
          returnKeyType="go"
          onSubmitEditing={onLogin}
        />

        <Pressable
          style={[styles.button, !canSubmit && styles.buttonDisabled]}
          onPress={onLogin}
          disabled={!canSubmit}
        >
          <Text style={styles.buttonText}>{loading ? "Signing in…" : "Sign in"}</Text>
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  keyboardView: { flex: 1, backgroundColor: "#faf7f0" },
  container: { flexGrow: 1, justifyContent: "center", padding: 28 },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 32 },
  logoBox: {
    width: 48,
    height: 48,
    borderRadius: 12,
    backgroundColor: "#ff6f3d",
    alignItems: "center",
    justifyContent: "center",
  },
  logoText: { color: "#fff", fontWeight: "800", fontSize: 18 },
  appName: { fontSize: 20, fontWeight: "700", color: "#1f1f1f" },
  appTagline: { fontSize: 13, color: "#8a8175" },
  title: { fontSize: 17, fontWeight: "600", color: "#1f1f1f", marginBottom: 20 },
  validationError: {
    backgroundColor: "#fff5f1",
    borderRadius: 8,
    padding: 10,
    marginBottom: 12,
  },
  validationErrorText: { color: "#8f2d10", fontSize: 13 },
  input: {
    borderWidth: 1,
    borderColor: "#d4c8b7",
    borderRadius: 10,
    backgroundColor: "#fff",
    padding: 13,
    marginBottom: 12,
    fontSize: 15,
    color: "#1f1f1f",
  },
  button: {
    backgroundColor: "#ff6f3d",
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
    marginTop: 4,
  },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: "#1b1b1b", fontWeight: "700", fontSize: 15 },
});
