type PodHarness = {
  pod: typeof import("../pod");
  requestPodUploadUrl: jest.Mock;
  fetchMock: jest.MockedFunction<typeof fetch>;
};

function asResponse(
  payload: Partial<Response> & { ok: boolean; status?: number },
): Response {
  return {
    status: payload.status ?? (payload.ok ? 200 : 500),
    ...payload,
  } as Response;
}

function setupPodHarness(): PodHarness {
  jest.resetModules();

  const requestPodUploadUrl = jest.fn();
  jest.doMock("../api", () => ({
    requestPodUploadUrl,
  }));

  const fetchMock = jest.fn() as jest.MockedFunction<typeof fetch>;
  global.fetch = fetchMock;

  const pod = require("../pod") as typeof import("../pod");
  return {
    pod,
    requestPodUploadUrl,
    fetchMock,
  };
}

describe("pod service", () => {
  test("returns undefined when completion payload is absent", async () => {
    const harness = setupPodHarness();

    await expect(
      harness.pod.resolveStopCompletionPayload("route-1", "stop-1", undefined),
    ).resolves.toBeUndefined();
    expect(harness.requestPodUploadUrl).not.toHaveBeenCalled();
    expect(harness.fetchMock).not.toHaveBeenCalled();
  });

  test("returns existing hosted POD URLs without uploading", async () => {
    const harness = setupPodHarness();

    const payload = await harness.pod.resolveStopCompletionPayload("route-1", "stop-1", {
      photo_urls: ["https://cdn.example.com/pod/photo.jpg"],
      signature_url: "https://cdn.example.com/pod/signature.jpg",
      receiver_name: "Alex",
      otp_code: "123456",
      notes: "left with concierge",
    });

    expect(payload).toEqual({
      photo_urls: ["https://cdn.example.com/pod/photo.jpg"],
      signature_url: "https://cdn.example.com/pod/signature.jpg",
      receiver_name: "Alex",
      otp_code: "123456",
      notes: "left with concierge",
    });
    expect(harness.requestPodUploadUrl).not.toHaveBeenCalled();
    expect(harness.fetchMock).not.toHaveBeenCalled();
  });

  test("uploads local photo URI and resolves remote URL using inferred content type", async () => {
    const harness = setupPodHarness();

    harness.requestPodUploadUrl.mockResolvedValueOnce({
      upload_url: "https://upload.example.com/photo",
      file_url: "https://cdn.example.com/photo.png",
      object_key: "photo-key",
      expires_at: "2026-02-26T00:00:00.000Z",
    });
    harness.fetchMock
      .mockResolvedValueOnce(
        asResponse({
          ok: true,
          blob: async () => new Blob(["photo-bytes"], { type: "image/png" }),
        }),
      )
      .mockResolvedValueOnce(asResponse({ ok: true, status: 200 }));

    const payload = await harness.pod.resolveStopCompletionPayload("route-2", "stop-2", {
      notes: "front desk",
      photo_local_uris: ["file:///tmp/pod-image.png?cache=1"],
    });

    expect(harness.requestPodUploadUrl).toHaveBeenCalledWith(
      "route-2",
      "stop-2",
      "photo",
      "image/png",
    );
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "file:///tmp/pod-image.png?cache=1",
    );
    expect(String(harness.fetchMock.mock.calls[1][0])).toBe(
      "https://upload.example.com/photo",
    );
    expect(harness.fetchMock.mock.calls[1][1]).toMatchObject({
      method: "PUT",
      headers: { "Content-Type": "image/png" },
    });
    expect(payload).toEqual({
      photo_urls: ["https://cdn.example.com/photo.png"],
      signature_url: undefined,
      receiver_name: undefined,
      otp_code: undefined,
      notes: "front desk",
    });
  });

  test("uploads local signature URI and defaults unknown extension to jpeg", async () => {
    const harness = setupPodHarness();

    harness.requestPodUploadUrl.mockResolvedValueOnce({
      upload_url: "https://upload.example.com/signature",
      file_url: "https://cdn.example.com/signature.jpg",
      object_key: "signature-key",
      expires_at: "2026-02-26T00:00:00.000Z",
    });
    harness.fetchMock
      .mockResolvedValueOnce(
        asResponse({
          ok: true,
          blob: async () => new Blob(["signature-bytes"], { type: "image/jpeg" }),
        }),
      )
      .mockResolvedValueOnce(asResponse({ ok: true, status: 200 }));

    const payload = await harness.pod.resolveStopCompletionPayload("route-3", "stop-3", {
      signature_local_uri: "file:///tmp/signature.unknown_ext",
    });

    expect(harness.requestPodUploadUrl).toHaveBeenCalledWith(
      "route-3",
      "stop-3",
      "signature",
      "image/jpeg",
    );
    expect(payload).toEqual({
      photo_urls: undefined,
      signature_url: "https://cdn.example.com/signature.jpg",
      receiver_name: undefined,
      otp_code: undefined,
      notes: undefined,
    });
  });

  test("throws when local POD file cannot be read", async () => {
    const harness = setupPodHarness();

    harness.requestPodUploadUrl.mockResolvedValueOnce({
      upload_url: "https://upload.example.com/photo",
      file_url: "https://cdn.example.com/photo.jpg",
      object_key: "photo-key",
      expires_at: "2026-02-26T00:00:00.000Z",
    });
    harness.fetchMock.mockResolvedValueOnce(asResponse({ ok: false, status: 404 }));

    await expect(
      harness.pod.resolveStopCompletionPayload("route-4", "stop-4", {
        photo_local_uris: ["file:///tmp/missing.jpg"],
      }),
    ).rejects.toThrow("Failed to read local POD file for upload.");
  });

  test("throws when upload target rejects the file", async () => {
    const harness = setupPodHarness();

    harness.requestPodUploadUrl.mockResolvedValueOnce({
      upload_url: "https://upload.example.com/photo",
      file_url: "https://cdn.example.com/photo.jpg",
      object_key: "photo-key",
      expires_at: "2026-02-26T00:00:00.000Z",
    });
    harness.fetchMock
      .mockResolvedValueOnce(
        asResponse({
          ok: true,
          blob: async () => new Blob(["bytes"], { type: "image/jpeg" }),
        }),
      )
      .mockResolvedValueOnce(asResponse({ ok: false, status: 403 }));

    await expect(
      harness.pod.resolveStopCompletionPayload("route-5", "stop-5", {
        photo_local_uris: ["file:///tmp/photo.jpg"],
      }),
    ).rejects.toThrow("POD upload failed with status 403.");
  });

  test("skips PUT upload for dry-run presigned targets", async () => {
    const harness = setupPodHarness();

    harness.requestPodUploadUrl.mockResolvedValueOnce({
      upload_url: "https://delivery-pod-files.s3.us-east-1.amazonaws.com/pod/mock?mock_presigned=1",
      file_url: "https://cdn.example.com/photo.jpg",
      object_key: "photo-key",
      expires_at: "2026-02-26T00:00:00.000Z",
    });

    const payload = await harness.pod.resolveStopCompletionPayload("route-6", "stop-6", {
      photo_local_uris: ["file:///tmp/photo.jpg"],
    });

    expect(payload).toEqual({
      photo_urls: ["https://cdn.example.com/photo.jpg"],
      signature_url: undefined,
      receiver_name: undefined,
      otp_code: undefined,
      notes: undefined,
    });
    expect(harness.fetchMock).not.toHaveBeenCalled();
  });
});
