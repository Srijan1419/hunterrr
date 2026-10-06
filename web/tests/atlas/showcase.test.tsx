import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MatchDial } from "@/components/atlas/MatchDial";
import { StatTile } from "@/components/atlas/StatTile";
import { FilterToggle } from "@/components/atlas/FilterToggle";
import { Chip } from "@/components/atlas/Chip";
import { SkillTag } from "@/components/atlas/SkillTag";
import { ProvenanceChip } from "@/components/atlas/ProvenanceChip";
import { ScoreBar } from "@/components/atlas/ScoreBar";

/**
 * Showcase: every Atlas component rendered together with a spread of props
 * (all tones, all states, edge values), asserting every accessible name, role
 * and attribute from the spec. There is deliberately NO showcase page route.
 */
describe("Atlas showcase", () => {
  it("renders every component with a spread of props", () => {
    const onPressedChange = vi.fn();
    const { container } = render(
      <div>
        <MatchDial score={0} />
        <MatchDial score={100} />
        <MatchDial score={150} />
        <MatchDial score={-3} />
        <MatchDial score={91.6} />
        <StatTile value={128} label="Matches" />
        <StatTile value="3" label="Closing soon" tone="hot" />
        <FilterToggle label="Remote only" pressed={false} onPressedChange={onPressedChange} />
        <FilterToggle label="High match" pressed onPressedChange={onPressedChange} />
        <Chip>plain chip</Chip>
        <Chip tone="ok">ok chip</Chip>
        <Chip tone="hot">hot chip</Chip>
        <SkillTag skill="python" />
        <SkillTag skill="rust" matched />
        <ProvenanceChip source="jsonld" />
        <ProvenanceChip source="source" />
        <ProvenanceChip source="rule" />
        <ProvenanceChip source="llm" />
        <ProvenanceChip source="manual" />
        <ProvenanceChip source="unknown" />
        <ScoreBar label="Skills" points={7} max={10} />
        <ScoreBar label="Empty" points={5} max={0} />
      </div>
    );

    // MatchDial: role="img" + aria-label, edge values clamped/rounded.
    expect(screen.getAllByRole("img", { name: "Match score 0 out of 100" })).toHaveLength(2); // 0 and clamped -3
    expect(screen.getAllByRole("img", { name: "Match score 100 out of 100" })).toHaveLength(2); // 100 and clamped 150
    expect(screen.getByRole("img", { name: "Match score 92 out of 100" })).toBeInTheDocument(); // 91.6 rounded

    // StatTile: values, labels, hot tone colour.
    expect(screen.getByText("128")).toBeInTheDocument();
    expect(screen.getByText("Matches")).toBeInTheDocument();
    expect(screen.getByText("3")).toHaveStyle({ color: "var(--hot)" });

    // FilterToggle: real buttons, aria-pressed, label as accessible name.
    const off = screen.getByRole("button", { name: "Remote only" });
    const on = screen.getByRole("button", { name: "High match" });
    expect(off).toHaveAttribute("aria-pressed", "false");
    expect(on).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(off);
    expect(onPressedChange).toHaveBeenCalledWith(true);

    // Chip: all tones present, none interactive.
    expect(screen.getByText("plain chip")).toBeInTheDocument();
    expect(screen.getByText("ok chip")).toHaveStyle({
      background: "var(--accent-soft)",
      color: "var(--accent)",
    });
    const hotChip = screen.getByText("hot chip");
    expect(hotChip).toHaveStyle({
      background: "var(--hot-soft)",
      color: "var(--hot)",
      "font-weight": "600",
    });
    for (const el of [screen.getByText("plain chip"), screen.getByText("ok chip"), hotChip]) {
      expect(el.tagName).toBe("SPAN");
    }
    expect(screen.getAllByRole("button")).toHaveLength(2); // only the toggles

    // SkillTag: mono at --fs-1 (13px), matched filled with --accent.
    expect(screen.getByText("python")).toHaveStyle({ "font-size": "var(--fs-1)" });
    expect(screen.getByText("rust")).toHaveStyle({ background: "var(--accent)" });

    // ProvenanceChip: every source with its plain-words title.
    const titles: Record<string, string> = {
      jsonld: "Read from the job page's own data",
      source: "From the job board's data fields",
      rule: "Worked out by a fixed rule",
      llm: "Guessed by an AI model",
      manual: "Entered by you",
      unknown: "Not stated",
    };
    for (const [source, title] of Object.entries(titles)) {
      expect(screen.getByText(source)).toHaveAttribute("title", title);
    }

    // ScoreBar: labels, mono points/max, gradient fill, max=0 empty bar.
    expect(screen.getByText("Skills")).toBeInTheDocument();
    expect(screen.getByText("7/10")).toBeInTheDocument();
    expect(screen.getByText("5/0")).toBeInTheDocument();
    const fills = container.querySelectorAll(
      "span[style*='linear-gradient']"
    ) as NodeListOf<HTMLElement>;
    expect(fills.length).toBe(2);
    expect(fills[0].style.background).toContain("var(--accent)");
    expect(fills[0].style.background).toContain("var(--hot)");
    expect(fills[0].style.width).toBe("70%");
    expect(fills[1].style.width).toBe("0%");
  });
});
