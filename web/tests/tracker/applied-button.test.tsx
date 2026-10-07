import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const actions = vi.hoisted(() => ({ appliedToJob: vi.fn() }));
vi.mock("@/app/(app)/tracker/actions", () => actions);

import { AppliedButton } from "@/components/tracker/AppliedButton";

beforeEach(() => vi.clearAllMocks());

describe("AppliedButton", () => {
  it("one tap marks the job applied and then links to the tracker", async () => {
    actions.appliedToJob.mockResolvedValue({ ok: true, applicationId: 3 });
    render(<AppliedButton postingId={7} state={null} />);
    fireEvent.click(screen.getByRole("button", { name: "I applied" }));
    await waitFor(() => expect(screen.getByRole("link", { name: /Applied/ })).toBeInTheDocument());
    expect(actions.appliedToJob).toHaveBeenCalledWith(7);
  });

  it("a job that is only saved still offers the button; one already applied or further does not", () => {
    const { rerender } = render(<AppliedButton postingId={7} state="saved" />);
    expect(screen.getByRole("button", { name: "I applied" })).toBeInTheDocument();
    rerender(<AppliedButton postingId={7} state="interview" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("link", { name: /Applied/ })).toBeInTheDocument();
  });

  it("shows the error and keeps the button when the action fails", async () => {
    actions.appliedToJob.mockResolvedValue({ ok: false, error: "That job no longer exists." });
    render(<AppliedButton postingId={7} state={null} />);
    fireEvent.click(screen.getByRole("button", { name: "I applied" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no longer exists"));
    expect(screen.getByRole("button", { name: "I applied" })).toBeInTheDocument();
  });
});
