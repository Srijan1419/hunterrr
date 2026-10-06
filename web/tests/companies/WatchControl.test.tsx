import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const act = vi.hoisted(() => ({ setCompanyWatch: vi.fn() }));
vi.mock("@/app/(app)/companies/actions", () => act);

import { WatchControl } from "@/components/companies/WatchControl";

beforeEach(() => act.setCompanyWatch.mockReset());

describe("WatchControl", () => {
  it("watches, shows it at once, and pressing it again resets to neither", async () => {
    act.setCompanyWatch.mockResolvedValue({ ok: true });
    render(<WatchControl companyId={7} name="Acme" initial="none" />);
    fireEvent.click(screen.getByRole("button", { name: "Watch Acme" }));
    expect(screen.getByRole("button", { name: "Watch Acme" }).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(act.setCompanyWatch).toHaveBeenCalledWith(7, "watch"));
    fireEvent.click(screen.getByRole("button", { name: "Watch Acme" }));
    await waitFor(() => expect(act.setCompanyWatch).toHaveBeenLastCalledWith(7, "none"));
  });

  it("switching from watch to ignore sends ignore", async () => {
    act.setCompanyWatch.mockResolvedValue({ ok: true });
    render(<WatchControl companyId={7} name="Acme" initial="watch" />);
    fireEvent.click(screen.getByRole("button", { name: "Ignore Acme" }));
    await waitFor(() => expect(act.setCompanyWatch).toHaveBeenCalledWith(7, "ignore"));
    expect(screen.getByRole("button", { name: "Ignore Acme" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Watch Acme" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("puts the state back and says why when saving fails", async () => {
    act.setCompanyWatch.mockResolvedValue({ ok: false, error: "That company was not found." });
    render(<WatchControl companyId={7} name="Acme" initial="none" />);
    fireEvent.click(screen.getByRole("button", { name: "Ignore Acme" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/not found/));
    expect(screen.getByRole("button", { name: "Ignore Acme" }).getAttribute("aria-pressed")).toBe("false");
  });
});
