import { z } from "zod";

/**
 * The owner's profile: what decides which jobs fit. One definition shared by the form, the résumé
 * reader and the database (`hunterrr.profiles.data`, a new version on every save).
 *
 * Every field has a safe empty default, so a half-filled profile is always valid; nothing here is
 * guessed for the user.
 */

const text = (max: number) => z.string().trim().max(max);
const list = (maxItems: number, maxLen: number) =>
  z
    .array(z.string().trim().min(1).max(maxLen))
    .max(maxItems)
    // case-insensitive de-duplication, first spelling wins
    .transform((items) => items.filter((v, i) => items.findIndex((w) => w.toLowerCase() === v.toLowerCase()) === i));

export const ProfileSchema = z.object({
  name: text(120).default(""),
  /** One line, e.g. "Final-year B.Tech, Computer Science". */
  headline: text(200).default(""),
  education: text(200).default(""),
  graduationYear: z.number().int().min(1980).max(2040).nullable().default(null),
  /** Paid, full-time-equivalent experience in years (internships count as 0 unless stated). */
  experienceYears: z.number().min(0).max(50).nullable().default(null),
  targetRoles: list(10, 80).default([]),
  skills: list(60, 60).default([]),
  /** Role families (etl/decide/role_family.py ids, e.g. "customer-support") the owner wants; empty = no preference. */
  targetFamilies: z.array(z.string().regex(/^[a-z][a-z-]*$/)).max(8).default([]),
  /** Preferred cities or regions, as the owner writes them. */
  locations: list(15, 80).default([]),
  remoteOk: z.boolean().default(true),
  /** ISO 3166 alpha-2 countries the owner can work in without sponsorship. */
  workCountries: z.array(z.string().regex(/^[A-Z]{2}$/)).max(30).default(["IN"]),
  needsSponsorship: z.boolean().default(false),
  /** Minimum pay in INR lakh per year (LPA); null = no floor. */
  minPayLpa: z.number().min(0).max(1000).nullable().default(null),
});

export type Profile = z.infer<typeof ProfileSchema>;

export const EMPTY_PROFILE: Profile = ProfileSchema.parse({});

/** The fields a résumé can fill (preferences such as pay floor and remote are the owner's call). */
export const RESUME_FIELDS = [
  "name", "headline", "education", "graduationYear", "experienceYears", "targetRoles", "skills", "locations",
] as const satisfies readonly (keyof Profile)[];
export type ResumeFields = Partial<Pick<Profile, (typeof RESUME_FIELDS)[number]>>;
