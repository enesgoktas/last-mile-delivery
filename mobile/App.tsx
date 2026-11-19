import { useEffect, useRef } from "react";
import { NavigationContainer, createNavigationContainerRef } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import "./src/i18n";
import * as Sentry from "@sentry/react-native";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { ErrorBoundary } from "./src/components/ErrorBoundary";
import { NetworkBanner } from "./src/components/NetworkBanner";
import { configureNotificationHandler, registerNotificationResponseListener } from "./src/services/pushService";
import type { RootStackParamList } from "./src/types";

const SENTRY_DSN = process.env.EXPO_PUBLIC_SENTRY_DSN;
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tracesSampleRate: 0.1,
  });
}

const navigationRef = createNavigationContainerRef<RootStackParamList>();

function App() {
  const listenerRef = useRef<ReturnType<typeof registerNotificationResponseListener> | null>(null);

  useEffect(() => {
    configureNotificationHandler();
    listenerRef.current = registerNotificationResponseListener(navigationRef);
    return () => {
      listenerRef.current?.remove();
    };
  }, []);

  return (
    <ErrorBoundary>
      <NetworkBanner />
      <NavigationContainer ref={navigationRef}>
        <StatusBar style="dark" />
        <AppNavigator />
      </NavigationContainer>
    </ErrorBoundary>
  );
}

export default SENTRY_DSN ? Sentry.wrap(App) : App;
