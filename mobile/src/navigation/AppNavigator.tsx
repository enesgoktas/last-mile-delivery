import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { LoginScreen } from "../screens/LoginScreen";
import { MyRouteScreen } from "../screens/MyRouteScreen";
import { StopDetailScreen } from "../screens/StopDetailScreen";
import { LoadingChecklistScreen } from "../screens/LoadingChecklistScreen";
import type { RootStackParamList } from "../types";
import { getAccessToken } from "../services/auth";

const Stack = createNativeStackNavigator<RootStackParamList>();

function isTokenExpired(token: string): boolean {
  try {
    const payloadB64 = token.split(".")[1];
    if (!payloadB64) return true;
    const padded = payloadB64.replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(padded);
    const payload = JSON.parse(json) as { exp?: number };
    if (typeof payload.exp !== "number") return false;
    return Date.now() / 1000 >= payload.exp - 30;
  } catch {
    return true;
  }
}

export function AppNavigator() {
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [initialRoute, setInitialRoute] = useState<keyof RootStackParamList>("Login");

  useEffect(() => {
    const run = async () => {
      const token = await getAccessToken();
      const authenticated = token !== null && !isTokenExpired(token);
      setInitialRoute(authenticated ? "MyRoute" : "Login");
      setIsBootstrapping(false);
    };
    run();
  }, []);

  if (isBootstrapping) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#faf7f0" }}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <Stack.Navigator initialRouteName={initialRoute}>
      <Stack.Screen
        name="Login"
        component={LoginScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="MyRoute"
        component={MyRouteScreen}
        options={{ title: "My Route", headerBackVisible: false }}
      />
      <Stack.Screen
        name="StopDetail"
        component={StopDetailScreen}
        options={({ route }) => ({
          title: `Stop #${route.params.stop.sequence_number}`,
        })}
      />
      <Stack.Screen
        name="LoadingChecklist"
        component={LoadingChecklistScreen}
        options={{ title: "Loading Checklist" }}
      />
    </Stack.Navigator>
  );
}
