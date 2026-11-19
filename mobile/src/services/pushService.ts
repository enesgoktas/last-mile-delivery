import type { NavigationContainerRef } from "@react-navigation/native";
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { updateCourierPushToken } from "./api";
import type { PushProvider, RootStackParamList } from "../types";

export type PushRegistrationStatus =
  | "registered"
  | "simulator"
  | "permission_denied"
  | "unsupported_platform"
  | "invalid_token"
  | "failed";

export type PushRegistrationResult = {
  status: PushRegistrationStatus;
  message: string;
  token?: string;
};

function resolveExpoProjectId(): string | null {
  const easProjectId = Constants.easConfig?.projectId;
  if (typeof easProjectId === "string" && easProjectId.trim()) {
    return easProjectId.trim();
  }

  const configProjectId = (Constants.expoConfig?.extra as { eas?: { projectId?: unknown } } | undefined)
    ?.eas?.projectId;
  if (typeof configProjectId === "string" && configProjectId.trim()) {
    return configProjectId.trim();
  }

  return null;
}

export function configureNotificationHandler(): void {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowAlert: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

export async function registerCourierPushToken(courierId: string): Promise<PushRegistrationResult> {
  if (!Device.isDevice) {
    return {
      status: "simulator",
      message: "Push notifications require a physical device.",
    };
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") {
    const requested = await Notifications.requestPermissionsAsync();
    status = requested.status;
  }
  if (status !== "granted") {
    return {
      status: "permission_denied",
      message: "Notifications are disabled. Enable them to receive route updates.",
    };
  }

  try {
    const projectId = resolveExpoProjectId();
    if (!projectId) {
      return {
        status: "failed",
        message:
          "Expo project ID is missing. Configure EAS project metadata before enabling push notifications.",
      };
    }

    const tokenResult = await Notifications.getExpoPushTokenAsync({ projectId });
    const token = typeof tokenResult.data === "string" ? tokenResult.data : null;

    if (!token) {
      return {
        status: "invalid_token",
        message: "Push registration returned an empty Expo push token.",
      };
    }

    if (Platform.OS === "android") {
      await Notifications.setNotificationChannelAsync("default", {
        name: "default",
        importance: Notifications.AndroidImportance.DEFAULT,
      });
    }

    const pushProvider: PushProvider = "EXPO";
    await updateCourierPushToken(courierId, {
      push_provider: pushProvider,
      push_token: token,
    });
    return {
      status: "registered",
      message: "Push notifications are ready.",
      token,
    };
  } catch (error) {
    return {
      status: "failed",
      message:
        error instanceof Error
          ? error.message
          : "Push notification registration failed.",
    };
  }
}

export function getPushRegistrationWarning(
  result: Pick<PushRegistrationResult, "status" | "message"> | null,
): string | null {
  if (!result || result.status === "registered") {
    return null;
  }

  if (result.status === "permission_denied") {
    return result.message;
  }

  if (result.status === "unsupported_platform") {
    return result.message;
  }

  if (result.status === "simulator") {
    return result.message;
  }

  if (result.status === "invalid_token") {
    return result.message;
  }

  return `Push notification registration failed: ${result.message}`;
}

export function registerNotificationResponseListener(
  navigationRef: NavigationContainerRef<RootStackParamList>,
): Notifications.Subscription {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    const data = response.notification.request.content.data as Record<string, unknown>;
    const screen = data?.screen as string | undefined;

    if (!navigationRef.isReady()) return;

    if (screen === "StopDetail" && data?.stop) {
      try {
        const stop = data.stop as RootStackParamList["StopDetail"]["stop"];
        const routeId = data.routeId as string;
        navigationRef.navigate("StopDetail", { routeId, stop });
      } catch {
        // Malformed notification payload — ignore silently.
      }
    } else if (screen === "MyRoute") {
      navigationRef.navigate("MyRoute");
    }
  });
}
