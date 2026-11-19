import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import NetInfo from "@react-native-community/netinfo";

export function NetworkBanner() {
  const [isOffline, setIsOffline] = useState(false);

  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((state) => {
      setIsOffline(state.isConnected === false);
    });
    return unsubscribe;
  }, []);

  if (!isOffline) {
    return null;
  }

  return (
    <View style={styles.banner}>
      <Text style={styles.bannerText}>No internet connection — changes will sync when back online</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: "#1e293b",
    paddingVertical: 8,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  bannerText: {
    color: "#f8fafc",
    fontSize: 12,
    fontWeight: "500",
    textAlign: "center",
  },
});
