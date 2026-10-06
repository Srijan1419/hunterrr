import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Description, parseDescription } from "@/components/feed/Description";

describe("parseDescription", () => {
  it("makes headings, bullet lists and paragraphs from blank-line separated blocks", () => {
    const blocks = parseDescription(
      "About the role\n\nYou will build dashboards.\nYou will talk to users.\n\nResponsibilities:\n\n- Ship projects\n- Work with a mentor\n\n* Present your work",
    );
    expect(blocks).toEqual([
      { kind: "heading", text: "About the role" },
      { kind: "paragraph", lines: ["You will build dashboards.", "You will talk to users."] },
      { kind: "heading", text: "Responsibilities" }, // the trailing colon is dropped
      { kind: "list", items: ["Ship projects", "Work with a mentor"] },
      { kind: "list", items: ["Present your work"] },
    ]);
  });

  it("does not take a sentence, a long line or a bullet for a heading", () => {
    expect(parseDescription("We are hiring.")[0].kind).toBe("paragraph");
    expect(parseDescription("x".repeat(80))[0].kind).toBe("paragraph");
    expect(parseDescription("- one")[0].kind).toBe("list");
  });

  it("copes with empty text, Windows line endings and runs of blank lines", () => {
    expect(parseDescription("")).toEqual([]);
    expect(parseDescription("\n\n  \n")).toEqual([]);
    expect(parseDescription("A\r\n\r\n\r\n\r\n- b\r\n")).toEqual([
      { kind: "heading", text: "A" },
      { kind: "list", items: ["b"] },
    ]);
  });

  it("stops at a sane number of blocks", () => {
    expect(parseDescription(Array.from({ length: 1000 }, (_, i) => `Block ${i}.`).join("\n\n")).length).toBe(400);
  });
});

describe("Description", () => {
  it("renders text only: markup in a posting stays visible text and never becomes an element", () => {
    const { container } = render(<Description text={"<img src=x onerror=alert(1)>\n\n- <script>alert(1)</script>"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.querySelectorAll("li")).toHaveLength(1);
  });

  it("uses real heading, list and paragraph elements", () => {
    const { container } = render(<Description text={"Requirements\n\n- SQL\n- Python\n\nSend your résumé.\nThanks."} />);
    expect(container.querySelector("h3")?.textContent).toBe("Requirements");
    expect(Array.from(container.querySelectorAll("li")).map((l) => l.textContent)).toEqual(["SQL", "Python"]);
    expect(container.querySelector("p")?.innerHTML).toBe("Send your résumé.<br>Thanks.");
  });
});
