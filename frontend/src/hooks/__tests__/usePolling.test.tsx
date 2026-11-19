import { StrictMode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { usePolling } from "../usePolling";

describe("usePolling", () => {
  it("loads data immediately and then polls on interval", async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValue("second")
      .mockResolvedValueOnce("first");

    const { result } = renderHook(() => usePolling(fetcher, 200));

    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.data).toBe("first"), { timeout: 150 });

    await waitFor(() => expect(result.current.data).toBe("second"), { timeout: 1_000 });
    expect(fetcher.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it("stores error message when polling fetcher fails", async () => {
    const fetcher = vi.fn<() => Promise<string>>().mockRejectedValue(new Error("network failed"));

    const { result } = renderHook(() => usePolling(fetcher, 1_000));

    await waitFor(() => expect(result.current.error).toBe("network failed"));
    expect(result.current.data).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it("refetch triggers an immediate new polling run", async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce("one")
      .mockResolvedValueOnce("two");

    const { result } = renderHook(() => usePolling(fetcher, 30_000));

    await waitFor(() => expect(result.current.data).toBe("one"));

    act(() => {
      result.current.refetch();
    });

    await waitFor(() => expect(result.current.data).toBe("two"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not poll when disabled", async () => {
    const fetcher = vi.fn<() => Promise<string>>().mockResolvedValue("unused");

    const { result } = renderHook(() => usePolling(fetcher, 1_000, false));

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data).toBeNull();
  });

  it("does not start overlapping polling requests while a prior run is still pending", async () => {
    vi.useFakeTimers();
    try {
      let resolveFirstCall: ((value: string) => void) | undefined;
      const fetcher = vi
        .fn<() => Promise<string>>()
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveFirstCall = resolve;
            }),
        )
        .mockResolvedValue("second");

      const { result } = renderHook(() => usePolling(fetcher, 100));

      expect(fetcher).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(300);
        await Promise.resolve();
      });

      expect(fetcher).toHaveBeenCalledTimes(1);

      await act(async () => {
        resolveFirstCall?.("first");
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(fetcher).toHaveBeenCalledTimes(2);
      expect(result.current.data).toBe("second");
      expect(result.current.isLoading).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("resets its mounted guard correctly under React StrictMode", async () => {
    const fetcher = vi.fn<() => Promise<string>>().mockResolvedValue("loaded");

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <StrictMode>{children}</StrictMode>
    );

    const { result } = renderHook(() => usePolling(fetcher, 1_000), { wrapper });

    await waitFor(() => expect(result.current.data).toBe("loaded"));
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });
});
