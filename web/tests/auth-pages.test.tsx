import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const social = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth/client", () => ({ authClient: { signIn: { social } } }));

import SigninPage from "@/app/(auth)/signin/page";
import NotAllowedPage from "@/app/(auth)/not-allowed/page";

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_AUTH", "1");
  social.mockReset();
});
afterEach(() => vi.unstubAllEnvs());

describe("sign-in page", () => {
  it("offers exactly one way in: Google (no email or password fields that cannot work)", () => {
    render(<SigninPage />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Continue with Google/ })).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });

  it("shows it is working when pressed, and recovers with a message if Google cannot be reached", async () => {
    social.mockRejectedValueOnce(new Error("offline"));
    render(<SigninPage />);
    fireEvent.click(screen.getByRole("button", { name: /Continue with Google/ }));
    expect(social).toHaveBeenCalledWith({ provider: "google", callbackURL: "/jobs" });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/Could not reach Google/));
    expect((screen.getByRole("button", { name: /Continue with Google/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("says so plainly when Google sign-in is not configured", () => {
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_AUTH", "");
    render(<SigninPage />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/not set up/)).toBeTruthy();
  });
});

describe("not-allowed page", () => {
  it("explains what happened and offers one way back", () => {
    render(<NotAllowedPage />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/can.t sign in/);
    expect(screen.getByRole("link", { name: /different Google account/ }).getAttribute("href")).toBe("/signin");
  });
});
