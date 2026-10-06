import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const act = vi.hoisted(() => ({ reportWrong: vi.fn() }));
vi.mock("@/app/(app)/jobs/[id]/actions", () => act);

import { WrongButton } from "@/components/feed/WrongButton";
import { REPORTS_EMPTY, Reports } from "@/components/sources/Reports";

beforeEach(() => act.reportWrong.mockReset());

describe("WrongButton", () => {
  it("opens, sends the chosen field and note, and thanks the reader", async () => {
    act.reportWrong.mockResolvedValue({ ok: true, duplicate: false });
    render(<WrongButton postingId={12} />);
    fireEvent.click(screen.getByRole("button", { name: "Something wrong?" }));
    fireEvent.click(screen.getByLabelText("It is not really remote"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "says hybrid in the text" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(act.reportWrong).toHaveBeenCalledWith(12, "remote", "says hybrid in the text"));
    expect((await screen.findByRole("status")).textContent).toMatch(/Thanks, noted/);
  });

  it("defaults to the India problem, can be cancelled, and says so for a repeat", async () => {
    act.reportWrong.mockResolvedValue({ ok: true, duplicate: true });
    render(<WrongButton postingId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Something wrong?" }));
    expect((screen.getByLabelText("People in India can't apply") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Something wrong?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Something wrong?" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/Already reported/);
  });

  it("shows the error and stays open when sending fails", async () => {
    act.reportWrong.mockResolvedValue({ ok: false, error: "That job no longer exists." });
    render(<WrongButton postingId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Something wrong?" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/no longer exists/));
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });
});

describe("Reports list", () => {
  const now = new Date("2026-10-06T12:00:00Z");
  it("says what to do when there is nothing", () => {
    render(<Reports items={[]} now={now} />);
    expect(screen.getByText(REPORTS_EMPTY)).toBeTruthy();
  });

  it("lists each report with its job, what is wrong and the note", () => {
    render(<Reports now={now} items={[{ id: 1, postingId: 5, title: "Support Associate", company: "Acme", field: "india", note: "needs US work authorisation", createdAt: "2026-10-06T10:00:00Z" }]} />);
    expect(screen.getByRole("link", { name: "Support Associate" }).getAttribute("href")).toBe("/jobs/5");
    expect(screen.getByText(/People in India can't apply/)).toBeTruthy();
    expect(screen.getByText("needs US work authorisation")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Reported as wrong \(1\)/ })).toBeTruthy();
  });
});
