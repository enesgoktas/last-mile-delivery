import { shouldQueueOfflineOnError, toErrorMessage } from "./stopDetailHelpers";

export function buildRouteStartFailureMessage(error: unknown): string {
  const baseMessage = toErrorMessage(error);
  if (!shouldQueueOfflineOnError(error)) {
    return baseMessage;
  }

  return `${baseMessage}\n\nRoute start requires a live connection and is not queued offline. Reconnect and try again.`;
}
