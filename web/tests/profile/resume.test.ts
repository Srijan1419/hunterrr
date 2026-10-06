/**
 * @vitest-environment node
 */
import { describe, expect, it, vi } from "vitest";
import { ResumeError, checkAnswer, firstJsonObject, inResume, pdfText, readResumeText } from "@/lib/profile/resume";

const RESUME = `Priya Sharma
Bengaluru, Karnataka | priya@example.com
B.Tech in Computer Science, PES University, 2026
Skills: SQL, Python, Power BI, Excel, C
Projects: Sales dashboard in Power BI; churn model in Python.
Intern, Data Analytics, Acme (May 2025 - Jul 2025)`;

describe("inResume", () => {
  it("matches whole words and phrases only", () => {
    expect(inResume("Power BI", RESUME)).toBe(true);
    expect(inResume("power   bi", RESUME)).toBe(true);
    expect(inResume("C", RESUME)).toBe(true); // listed on its own
    expect(inResume("R", RESUME)).toBe(false); // "R" only occurs inside words
    expect(inResume("Java", RESUME)).toBe(false);
    expect(inResume("", RESUME)).toBe(false);
  });
});

describe("firstJsonObject", () => {
  it("finds the object inside fences or prose, and gives null for junk", () => {
    expect(firstJsonObject('```json\n{"a":1}\n```')).toEqual({ a: 1 });
    expect(firstJsonObject('Here you go: {"a":[1,2]} hope it helps')).toEqual({ a: [1, 2] });
    expect(firstJsonObject("no json")).toBeNull();
    expect(firstJsonObject("{broken")).toBeNull();
  });
});

describe("checkAnswer", () => {
  it("keeps values that fit and appear in the résumé, drops invented ones", () => {
    const { fields, dropped } = checkAnswer(
      {
        name: "Priya Sharma",
        headline: "Final-year B.Tech, Computer Science",
        education: "B.Tech in Computer Science, PES University",
        graduationYear: 2026,
        experienceYears: 0,
        targetRoles: ["Data Analyst", "Business Analyst"],
        skills: ["SQL", "Python", "Power BI", "Tableau", "Kubernetes"], // last two are not in the résumé
        locations: ["Bengaluru", "Mumbai"], // Mumbai is not in it
      },
      RESUME,
    );
    expect(fields.name).toBe("Priya Sharma");
    expect(fields.skills).toEqual(["SQL", "Python", "Power BI"]);
    expect(fields.locations).toEqual(["Bengaluru"]);
    expect(fields.graduationYear).toBe(2026);
    expect(fields.experienceYears).toBe(0);
    expect(fields.targetRoles).toEqual(["Data Analyst", "Business Analyst"]);
    expect(dropped).toBe(3);
  });

  it("drops a name or year that is not in the résumé and anything off-schema", () => {
    const { fields, dropped } = checkAnswer(
      { name: "Someone Else", graduationYear: 2031, experienceYears: -2, skills: "SQL", extra: "ignored" },
      RESUME,
    );
    expect(fields).toEqual({});
    expect(dropped).toBe(3);
  });

  it("copes with a non-object answer", () => {
    expect(checkAnswer(null, RESUME)).toEqual({ fields: {}, dropped: 0 });
    expect(checkAnswer("text", RESUME)).toEqual({ fields: {}, dropped: 0 });
  });
});

function reply(content: string, status = 200) {
  return new Response(JSON.stringify({ choices: [{ message: { content } }] }), { status });
}

describe("readResumeText", () => {
  it("falls back from Groq to NVIDIA and checks the answer", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(new Response("busy", { status: 503 }))
      .mockResolvedValueOnce(reply('{"name":"Priya Sharma","skills":["SQL","Rust"]}'));
    const r = await readResumeText(RESUME, { GROQ_API_KEY: "g", NVIDIA_API_KEY: "n" }, fetchImpl as never);
    expect(r.provider).toBe("nvidia");
    expect(r.fields).toEqual({ name: "Priya Sharma", skills: ["SQL"] });
    expect(r.dropped).toBe(1);
    const [groqUrl] = fetchImpl.mock.calls[0];
    const [nvUrl, nvInit] = fetchImpl.mock.calls[1];
    expect(groqUrl).toContain("api.groq.com");
    expect(nvUrl).toContain("integrate.api.nvidia.com");
    // the résumé travels as data inside delimiters; the instructions say never to follow it
    const body = JSON.parse(nvInit.body);
    expect(body.messages[1].content).toContain("<resume>");
    expect(body.messages[0].content).toMatch(/never follow instructions/i);
  });

  it("only ever calls Groq or NVIDIA, and says so when neither key is set", async () => {
    const fetchImpl = vi.fn();
    await expect(readResumeText(RESUME, { OPENROUTER_API_KEY: "x", GEMINI_API_KEY: "y" }, fetchImpl as never))
      .rejects.toBeInstanceOf(ResumeError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("gives a clear error when every provider fails", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("network"));
    await expect(readResumeText(RESUME, { GROQ_API_KEY: "g" }, fetchImpl as never)).rejects.toThrow(/did not answer/);
  });
});

describe("pdfText guards", () => {
  it("rejects empty, non-PDF and oversized input before parsing", async () => {
    await expect(pdfText(new Uint8Array())).rejects.toThrow(/empty/);
    await expect(pdfText(new TextEncoder().encode("hello, not a pdf"))).rejects.toThrow(/not a PDF/);
    await expect(pdfText(new Uint8Array(4 * 1024 * 1024 + 1))).rejects.toThrow(/larger than 4 MB/);
  });
});
