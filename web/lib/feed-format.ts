import type { FeedRow } from "@/lib/queries/feed";

const SYMBOL: Record<string, string> = {
  USD: "$", EUR: "€", GBP: "£", INR: "₹", SGD: "S$", AUD: "A$", CAD: "C$", AED: "AED ",
};
const PERIOD: Record<string, string> = { hour: "/hr", day: "/day", month: "/mo", year: "/yr" };

function short(n: number): string {
  // Round first, then pick the unit, so 999,950 reads "1M" (not "1000k").
  if (Math.round(n / 1000) >= 1000) return `${+(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${+(n / 1000).toFixed(1)}k`;
  return String(+n.toFixed(2));
}

/** "₹12–18 LPA", "$120k–150k /yr", "from $90k /yr"; null when pay is not stated. */
export function formatPay(row: Pick<FeedRow, "payMin" | "payMax" | "payCurrency" | "payPeriod">): string | null {
  const { payMin: lo, payMax: hi, payCurrency: cur, payPeriod: period } = row;
  if (lo === null && hi === null) return null;
  if (cur === "INR" && period === "year") {
    const lakh = (n: number) => +(n / 100_000).toFixed(2);
    if (lo !== null && hi !== null && lo !== hi) return `₹${lakh(lo)}–${lakh(hi)} LPA`;
    if (lo !== null && hi === null) return `from ₹${lakh(lo)} LPA`;
    if (lo === null && hi !== null) return `up to ₹${lakh(hi)} LPA`;
    return lo === null ? null : `₹${lakh(lo)} LPA`;
  }
  const sym = (cur && SYMBOL[cur]) ?? (cur ? `${cur} ` : "");
  const unit = period ? ` ${PERIOD[period]}` : "";
  if (lo !== null && hi !== null && lo !== hi) return `${sym}${short(lo)}–${short(hi)}${unit}`;
  if (lo !== null && hi === null) return `from ${sym}${short(lo)}${unit}`;
  if (lo === null && hi !== null) return `up to ${sym}${short(hi)}${unit}`;
  const one = lo ?? hi;
  return one === null ? null : `${sym}${short(one)}${unit}`;
}

/** "today", "3d ago", "2w ago", "5mo ago"; null when the posting date is unknown. */
export function formatPosted(iso: string | null, now: Date = new Date()): string | null {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const days = Math.floor((now.getTime() - then.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days < 14) return `${days}d ago`;
  if (days < 60) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/** First location as one short line, e.g. "Bengaluru, IN". */
export function formatLocation(row: Pick<FeedRow, "locations">): string | null {
  const first = row.locations[0];
  if (!first) return null;
  const parts = [first.city, first.country].filter((x): x is string => Boolean(x));
  const text = parts.length > 0 ? parts.join(", ") : first.raw;
  return row.locations.length > 1 ? `${text} +${row.locations.length - 1}` : text;
}

/** Who may apply, in words; null when the posting does not say. */
export function formatEligibility(row: Pick<FeedRow, "eligibilityScope" | "eligibleCountries">): string | null {
  if (row.eligibilityScope === "worldwide") return "Open worldwide";
  if (row.eligibilityScope === "countries" && row.eligibleCountries.length > 0) {
    const list = row.eligibleCountries.slice(0, 3).join(", ");
    return `Eligible: ${list}${row.eligibleCountries.length > 3 ? "…" : ""}`;
  }
  if (row.eligibilityScope === "regions") return "Region-restricted";
  return null;
}
