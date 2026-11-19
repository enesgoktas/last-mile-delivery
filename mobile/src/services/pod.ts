import { requestPodUploadUrl, type StopCompletionPayload } from "./api";

export type StopCompletionWithLocalPodPayload = StopCompletionPayload & {
  photo_local_uris?: string[];
  signature_local_uri?: string;
};

const MIME_BY_EXTENSION: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  heic: "image/heic",
};

function inferContentType(uri: string): string {
  // Data URIs from the signature canvas carry the MIME type inline.
  if (uri.startsWith("data:")) {
    const match = uri.match(/^data:([^;]+);/);
    return match?.[1] ?? "image/png";
  }
  const normalized = uri.split("?")[0].toLowerCase();
  const ext = normalized.split(".").pop();
  if (!ext) {
    return "image/jpeg";
  }
  return MIME_BY_EXTENSION[ext] ?? "image/jpeg";
}

/** Convert a base64 data URI to a Blob without any file-system dependency. */
function dataUriToBlob(dataUri: string, mimeType: string): Blob {
  const base64 = dataUri.split(",")[1];
  const byteChars = atob(base64);
  const byteNums = new Uint8Array(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) {
    byteNums[i] = byteChars.charCodeAt(i);
  }
  return new Blob([byteNums], { type: mimeType });
}

async function uploadLocalUri(uploadUrl: string, localUri: string, contentType: string) {
  // Local/dev backend can return dry-run presigned URLs. They are intentionally
  // non-uploadable and should be treated as "upload accepted" for demo flows.
  if (uploadUrl.includes("mock_presigned=1")) {
    return;
  }

  let fileBlob: Blob;

  if (localUri.startsWith("data:")) {
    // Signature canvas returns base64 data URIs; convert to Blob directly.
    fileBlob = dataUriToBlob(localUri, contentType);
  } else {
    const fileResponse = await fetch(localUri);
    if (!fileResponse.ok) {
      throw new Error("Failed to read local POD file for upload.");
    }
    fileBlob = await fileResponse.blob();
  }

  const uploadResponse = await fetch(uploadUrl, {
    method: "PUT",
    headers: {
      "Content-Type": contentType,
    },
    body: fileBlob,
  });
  if (!uploadResponse.ok) {
    throw new Error(`POD upload failed with status ${uploadResponse.status}.`);
  }
}

async function uploadPodAsset(
  routeId: string,
  stopId: string,
  fileType: "photo" | "signature",
  localUri: string,
): Promise<string> {
  const contentType = inferContentType(localUri);
  const uploadTarget = await requestPodUploadUrl(routeId, stopId, fileType, contentType);
  await uploadLocalUri(uploadTarget.upload_url, localUri, contentType);
  return uploadTarget.file_url;
}

export async function resolveStopCompletionPayload(
  routeId: string,
  stopId: string,
  payload?: StopCompletionWithLocalPodPayload,
): Promise<StopCompletionPayload | undefined> {
  if (!payload) {
    return undefined;
  }

  let photoUrls = payload.photo_urls;
  let signatureUrl = payload.signature_url;

  if ((!photoUrls || photoUrls.length === 0) && payload.photo_local_uris?.length) {
    // Upload all photos in parallel; fail fast if any upload fails.
    const results = await Promise.allSettled(
      payload.photo_local_uris.map((uri) => uploadPodAsset(routeId, stopId, "photo", uri)),
    );
    const failed = results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
    if (failed.length > 0) {
      throw new Error(
        `${failed.length} photo upload(s) failed: ${failed[0].reason instanceof Error ? failed[0].reason.message : String(failed[0].reason)}`,
      );
    }
    photoUrls = results
      .filter((r): r is PromiseFulfilledResult<string> => r.status === "fulfilled")
      .map((r) => r.value);
  }

  if (!signatureUrl && payload.signature_local_uri) {
    signatureUrl = await uploadPodAsset(routeId, stopId, "signature", payload.signature_local_uri);
  }

  return {
    photo_urls: photoUrls,
    signature_url: signatureUrl,
    receiver_name: payload.receiver_name,
    otp_code: payload.otp_code,
    notes: payload.notes,
  };
}
