import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";
import { ToastContainer } from "../components/Toast";
import { AssignProvider } from "../state/AssignContext";
import { ToastProvider } from "../state/ToastContext";

export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  return render(ui, {
    wrapper: ({ children }) => (
      <ToastProvider>
        <AssignProvider>{children}</AssignProvider>
        <ToastContainer />
      </ToastProvider>
    ),
    ...options,
  });
}
