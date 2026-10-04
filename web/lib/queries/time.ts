/**
 * Any database timestamp as an ISO-8601 UTC string, or null.
 *
 * The production driver hands back `Date` objects; other drivers (and the in-memory test database)
 * hand back text such as "2026-10-04 15:10:00+05". Normalising here keeps every page and test
 * independent of the driver and of the session time zone.
 */
export function toIso(value: unknown): string | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.toISOString();
  if (typeof value !== "string" || value.trim() === "") return null;
  let text = value.trim().replace(" ", "T");
  text = text.replace(/([+-]\d{2})$/, "$1:00"); // "+05" -> "+05:00"
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}
