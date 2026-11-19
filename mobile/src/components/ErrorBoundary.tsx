import { Component } from "react";
import type { ReactNode, ErrorInfo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <View style={styles.container}>
          <Text style={styles.title}>Something went wrong</Text>
          <Text style={styles.message}>{this.state.error.message}</Text>
          <Pressable style={styles.button} onPress={() => this.setState({ error: null })}>
            <Text style={styles.buttonText}>Try again</Text>
          </Pressable>
        </View>
      );
    }
    return this.props.children;
  }
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#faf7f0",
    padding: 28,
  },
  title: { fontSize: 20, fontWeight: "700", color: "#1f1f1f", marginBottom: 10, textAlign: "center" },
  message: { fontSize: 13, color: "#6c665c", textAlign: "center", marginBottom: 24 },
  button: {
    backgroundColor: "#ff6f3d",
    borderRadius: 10,
    paddingHorizontal: 24,
    paddingVertical: 12,
  },
  buttonText: { color: "#1b1b1b", fontWeight: "700" },
});
