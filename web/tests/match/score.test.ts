import { describe, expect, it } from "vitest";
import { scoreMatch, type MatchInput } from "@/lib/match/score";
import { ProfileSchema } from "@/lib/profile/schema";

const me = ProfileSchema.parse({
  skills: ["SQL", "Python", "Power BI", "Excel"],
  targetRoles: ["Data Analyst", "Business Analyst"],
  locations: ["Bengaluru"],
  experienceYears: 0,
  minPayLpa: 6,
});

const base: MatchInput = {
  title: "Data Analyst Intern", description: "", seniority: null, experienceMin: null, experienceMax: null,
  remoteType: null, locations: [], eligibilityScope: null, eligibleCountries: [],
  payMin: null, payMax: null, payCurrency: null, payPeriod: null,
};
const part = (r: ReturnType<typeof scoreMatch>, key: string) => r.parts.find((p) => p.key === key);

describe("scoreMatch", () => {
  it("scores a strong, fully stated fit near the top, with a readable breakdown", () => {
    const r = scoreMatch(me, {
      ...base, description: "You will use SQL, Python and Power BI daily.", seniority: "intern",
      remoteType: "onsite", locations: [{ raw: "Bengaluru, India", city: "Bengaluru", country: "IN" }],
      eligibilityScope: "countries", eligibleCountries: ["IN"],
      payMin: 600_000, payMax: 900_000, payCurrency: "INR", payPeriod: "year",
    });
    expect(r.score).toBeGreaterThanOrEqual(90);
    expect(r.blocked).toBeNull();
    expect(part(r, "skills")?.note).toMatch(/SQL, Python, Power BI/);
    expect(part(r, "role")?.points).toBe(25);
    expect(r.flags).toEqual([]);
  });

  it("excludes what the posting does not state, flags it, and never counts it for or against", () => {
    const r = scoreMatch(me, { ...base, description: "SQL and Python." });
    expect(r.parts.map((p) => p.key).sort()).toEqual(["role", "skills"]);
    expect(r.flags).toEqual(expect.arrayContaining(["Level not stated", "Location not stated", "Who may apply is not stated"]));
    expect(r.score).toBeGreaterThan(50); // judged only on skills and role
  });

  it("matches skills as whole words (SQL is not 'mysql'; C++ and C# keep their symbols)", () => {
    const sql = scoreMatch(me, { ...base, description: "We use MySQL and PostgreSQL." });
    expect(part(sql, "skills")?.points).toBe(0);
    const c = scoreMatch({ ...me, skills: ["C++", "C#"] }, { ...base, description: "Strong C++ and C# skills." });
    expect(part(c, "skills")?.points).toBe(Math.round(40 * (2 / 2) * 10) / 10);
  });

  it("caps a blocked job: senior level, or only open to countries you cannot work in", () => {
    const senior = scoreMatch(me, { ...base, title: "Senior Data Analyst", description: "SQL Python", seniority: "senior" });
    expect(senior.blocked).toMatch(/senior/);
    expect(senior.score).toBeLessThanOrEqual(25);
    const us = scoreMatch(me, { ...base, description: "SQL Python Power BI Excel", eligibilityScope: "countries", eligibleCountries: ["US"] });
    expect(us.blocked).toBe("Only open to US");
    expect(us.score).toBeLessThanOrEqual(25);
  });

  it("judges place by your remote preference and named cities", () => {
    expect(part(scoreMatch(me, { ...base, remoteType: "remote" }), "place")?.points).toBe(10);
    expect(part(scoreMatch({ ...me, remoteOk: false }, { ...base, remoteType: "remote" }), "place")?.points).toBe(0);
    const away = scoreMatch(me, { ...base, locations: [{ raw: "Pune, India", city: "Pune", country: "IN" }] });
    expect(part(away, "place")?.points).toBe(6);
  });

  it("judges pay only against a floor and only for yearly rupee pay", () => {
    const low = scoreMatch(me, { ...base, payMax: 400_000, payCurrency: "INR", payPeriod: "year" });
    expect(part(low, "pay")?.points).toBe(0);
    const usd = scoreMatch(me, { ...base, payMax: 90_000, payCurrency: "USD", payPeriod: "year" });
    expect(part(usd, "pay")).toBeUndefined();
    expect(usd.flags).toContain("Pay not stated in rupees per year");
    expect(part(scoreMatch({ ...me, minPayLpa: null }, { ...base, payMax: 1 }), "pay")).toBeUndefined();
  });

  it("an empty profile scores 0 and says what to add", () => {
    const r = scoreMatch(ProfileSchema.parse({}), base);
    expect(r.score).toBe(0);
    expect(r.flags).toEqual(expect.arrayContaining(["Add skills to your profile to score this", "Add target roles to your profile to score this"]));
  });
});

describe("scoreMatch with stored skills (matching v2)", () => {
  const withSkills = (skills: { skill: string; importance: "must" | "nice" }[], extra: Partial<MatchInput> = {}): MatchInput => ({
    ...base, title: "Business Analyst", description: "", seniority: "entry", remoteType: "remote",
    eligibilityScope: "countries", eligibleCountries: ["IN"], skills, ...extra,
  });
  const python = ProfileSchema.parse({ skills: ["Python", "SQL", "js"], targetRoles: ["Business Analyst"], experienceYears: 0.5 });

  it("maps your spellings to the dictionary and explains matched and missing must-haves", () => {
    const r = scoreMatch(python, withSkills([
      { skill: "sql", importance: "must" }, { skill: "python", importance: "must" }, { skill: "tableau", importance: "must" },
      { skill: "airflow", importance: "nice" },
    ]));
    const skills = part(r, "skills");
    expect(skills?.note).toMatch(/Matches 2 of 3 must-have skills \(SQL, Python\)\. Missing: Tableau/);
    expect(skills!.points).toBeGreaterThan(20);
  });

  it("covering 80% of the weighted ask is full credit; nothing covered is zero", () => {
    const all = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }, { skill: "python", importance: "must" }]));
    expect(part(all, "skills")?.points).toBe(40);
    const none = scoreMatch(python, withSkills([{ skill: "tableau", importance: "must" }, { skill: "power-bi", importance: "must" }]));
    expect(part(none, "skills")?.points).toBe(0);
  });

  it("a nice-to-have counts less than a must-have, and everyday skills count least", () => {
    const musts = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }, { skill: "tableau", importance: "must" }]));
    const nice = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }, { skill: "tableau", importance: "nice" }]));
    expect(part(nice, "skills")!.points).toBeGreaterThan(part(musts, "skills")!.points);
    const soft = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }, { skill: "communication", importance: "must" }]));
    expect(part(soft, "skills")!.points).toBeGreaterThanOrEqual(part(musts, "skills")!.points);
  });

  it("without stored skills it falls back to your skills found in the text", () => {
    const r = scoreMatch(python, { ...withSkills([]), description: "We use SQL and Python." });
    expect(part(r, "skills")?.note).toMatch(/Names Python, SQL/);
  });

  it("a role family you chose lifts the role part when the title is not one of your roles", () => {
    const p = ProfileSchema.parse({ skills: ["Zendesk"], targetRoles: ["Data Analyst"], targetFamilies: ["customer-support"] });
    const r = scoreMatch(p, withSkills([{ skill: "zendesk", importance: "must" }], { title: "Support Associate", roleFamily: "customer-support" }));
    expect(part(r, "role")?.points).toBe(20);
    expect(part(r, "role")?.note).toMatch(/Customer support/);
    const elsewhere = scoreMatch(p, withSkills([], { title: "Support Associate", roleFamily: "sales-bd" }));
    expect(part(elsewhere, "role")?.points).toBe(0);
  });

  it("freshness: today beats last week beats last month", () => {
    const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();
    const pts = (h: number) => part(scoreMatch(python, withSkills([], { postedAt: at(h) })), "freshness")?.points;
    expect(pts(3)).toBe(5);
    expect(pts(24 * 5)).toBe(3);
    expect(pts(24 * 20)).toBe(1.5);
    expect(pts(24 * 90)).toBe(0);
    expect(part(scoreMatch(python, withSkills([], { postedAt: null })), "freshness")).toBeUndefined();
  });

  it("buckets: strong needs a high score at a reachable level; a stretch is worth a shot; blocked is other", () => {
    const strong = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }, { skill: "python", importance: "must" }], { experienceMin: 1 }));
    expect(strong.score).toBeGreaterThanOrEqual(65);
    expect(strong.bucket).toBe("strong");
    const stretch = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }, { skill: "python", importance: "must" }], { experienceMin: 2 }));
    expect(stretch.bucket).toBe("worth");   // asks 2 years, you have 0.5: not "strong", still worth a shot
    const blocked = scoreMatch(python, withSkills([{ skill: "sql", importance: "must" }], { seniority: "senior" }));
    expect(blocked.bucket).toBe("other");
    const poor = scoreMatch(python, withSkills([{ skill: "tableau", importance: "must" }], { title: "Chef", experienceMin: 5 }));
    expect(poor.bucket).toBe("other");
  });
});
