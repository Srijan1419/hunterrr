import { describe, expect, it } from "vitest";
import { formatEligibility, formatLocation, formatPay, formatPosted } from "@/lib/feed-format";

const pay = (o: object) => ({ payMin: null, payMax: null, payCurrency: null, payPeriod: null, ...o }) as never;

describe("formatPay", () => {
  it("is null when pay is not stated", () => expect(formatPay(pay({}))).toBeNull());
  it("writes Indian yearly pay as LPA", () => {
    expect(formatPay(pay({ payMin: 1_200_000, payMax: 1_800_000, payCurrency: "INR", payPeriod: "year" }))).toBe("₹12–18 LPA");
    expect(formatPay(pay({ payMin: 1_500_000, payMax: 1_500_000, payCurrency: "INR", payPeriod: "year" }))).toBe("₹15 LPA");
  });
  it("writes ranges, floors and ceilings", () => {
    expect(formatPay(pay({ payMin: 120_000, payMax: 150_000, payCurrency: "USD", payPeriod: "year" }))).toBe("$120k–150k /yr");
    expect(formatPay(pay({ payMin: 90_000, payCurrency: "USD", payPeriod: "year" }))).toBe("from $90k /yr");
    expect(formatPay(pay({ payMax: 90_000, payCurrency: "USD", payPeriod: "year" }))).toBe("up to $90k /yr");
    expect(formatPay(pay({ payMin: 60, payMax: 60, payCurrency: "USD", payPeriod: "hour" }))).toBe("$60 /hr");
  });
});

describe("formatPosted", () => {
  const now = new Date("2026-10-03T12:00:00Z");
  it("is null for unknown or bad dates", () => {
    expect(formatPosted(null, now)).toBeNull();
    expect(formatPosted("nonsense", now)).toBeNull();
  });
  it("writes relative ages", () => {
    expect(formatPosted("2026-10-03T01:00:00Z", now)).toBe("today");
    expect(formatPosted("2026-09-30T12:00:00Z", now)).toBe("3d ago");
    expect(formatPosted("2026-09-12T12:00:00Z", now)).toBe("3w ago");
    expect(formatPosted("2026-05-03T12:00:00Z", now)).toBe("5mo ago");
  });
});

describe("formatLocation and formatEligibility", () => {
  it("joins city and country and counts extras", () => {
    const loc = { raw: "Pune, India", city: "Pune", region: null, country: "IN" };
    expect(formatLocation({ locations: [loc] })).toBe("Pune, IN");
    expect(formatLocation({ locations: [loc, loc] })).toBe("Pune, IN +1");
    expect(formatLocation({ locations: [{ raw: "Remote", city: null, region: null, country: null }] })).toBe("Remote");
    expect(formatLocation({ locations: [] })).toBeNull();
  });
  it("says only what the posting states", () => {
    expect(formatEligibility({ eligibilityScope: null, eligibleCountries: [] })).toBeNull();
    expect(formatEligibility({ eligibilityScope: "worldwide", eligibleCountries: [] })).toBe("Open worldwide");
    expect(formatEligibility({ eligibilityScope: "countries", eligibleCountries: ["IN", "US"] })).toBe("Eligible: IN, US");
    expect(formatEligibility({ eligibilityScope: "countries", eligibleCountries: ["IN", "US", "GB", "CA"] })).toBe("Eligible: IN, US, GB…");
  });
});
