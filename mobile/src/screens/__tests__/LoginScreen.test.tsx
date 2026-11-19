import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { login } from "../../services/api";
import { LoginScreen } from "../LoginScreen";

jest.mock("../../services/api", () => ({
  login: jest.fn(),
}));

const mockedLogin = login as jest.MockedFunction<typeof login>;

function renderLoginScreen() {
  const replace = jest.fn();
  const navigation = { replace };
  const route = { key: "Login-test", name: "Login" as const, params: undefined };
  const view = render(
    <LoginScreen navigation={navigation as never} route={route as never} />,
  );
  return { ...view, replace };
}

describe("LoginScreen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("shows tenant validation when tenant is empty", async () => {
    const { getByPlaceholderText, getByText } = renderLoginScreen();

    fireEvent.changeText(getByPlaceholderText("Tenant"), "");
    fireEvent.changeText(getByPlaceholderText("Email"), "a@b.com");
    fireEvent.changeText(getByPlaceholderText("Password"), "secret");

    // Submit is disabled when invalid; still invoke the same handler via password submit.
    fireEvent(getByPlaceholderText("Password"), "submitEditing");

    await waitFor(() => {
      expect(getByText("Tenant is required.")).toBeTruthy();
    });
    expect(mockedLogin).not.toHaveBeenCalled();
  });

  it("calls login with trimmed email and tenant then navigates to MyRoute", async () => {
    mockedLogin.mockResolvedValueOnce(undefined);
    const { getByPlaceholderText, getByText, replace } = renderLoginScreen();

    fireEvent.changeText(getByPlaceholderText("Tenant"), "  acme  ");
    fireEvent.changeText(getByPlaceholderText("Email"), "  Courier@Example.COM  ");
    fireEvent.changeText(getByPlaceholderText("Password"), "pw12345");

    fireEvent.press(getByText("Sign in"));

    await waitFor(() => {
      expect(mockedLogin).toHaveBeenCalledWith("courier@example.com", "pw12345", "acme");
      expect(replace).toHaveBeenCalledWith("MyRoute");
    });
  });

  it("shows invalid credentials message on 401", async () => {
    mockedLogin.mockRejectedValueOnce({ name: "ApiError", status: 401, message: "nope" });
    const { getByPlaceholderText, getByText } = renderLoginScreen();

    fireEvent.changeText(getByPlaceholderText("Tenant"), "t1");
    fireEvent.changeText(getByPlaceholderText("Email"), "a@b.com");
    fireEvent.changeText(getByPlaceholderText("Password"), "pw");

    fireEvent.press(getByText("Sign in"));

    await waitFor(() => {
      expect(
        getByText("Invalid credentials. Check your tenant, email, and password."),
      ).toBeTruthy();
    });
  });
});
