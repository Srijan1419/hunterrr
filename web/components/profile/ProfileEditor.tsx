"use client";

import { useRef, useState, useTransition, type DragEvent, type KeyboardEvent } from "react";
import { readResume, saveProfileAction } from "@/app/(app)/profile/actions";
import { FilterToggle } from "@/components/atlas/FilterToggle";
import { ProfileSchema, type Profile, type ResumeFields } from "@/lib/profile/schema";
import { ROLE_FAMILIES, SKILLS, skillIdsFromText } from "@/lib/skills/dictionary";
import styles from "./profile.module.css";

type Props = { initial: Profile; version: number | null; savedAt: string | null };
type ListKey = "targetRoles" | "skills" | "locations" | "workCountries";
type NumKey = "graduationYear" | "experienceYears" | "minPayLpa";

/**
 * Merge résumé fields into the form: lists gain the résumé's items (what was typed stays),
 * single values are filled only where the form is still empty (a typed value always wins).
 */
export function mergeResume(current: Profile, fields: ResumeFields): { next: Profile; filled: (keyof Profile)[] } {
  const next: Profile = { ...current };
  const filled: (keyof Profile)[] = [];
  for (const [k, v] of Object.entries(fields) as [keyof ResumeFields, unknown][]) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) {
      const have = next[k] as string[];
      const added = (v as string[]).filter((x) => !have.some((h) => h.toLowerCase() === x.toLowerCase()));
      if (added.length) {
        (next[k] as string[]) = [...have, ...added];
        filled.push(k);
      }
    } else if (next[k] === "" || next[k] === null) {
      (next as Record<string, unknown>)[k] = v;
      filled.push(k);
    }
  }
  return { next, filled };
}

function ChipsInput({
  id, values, onChange, placeholder, transform = (s) => s, listId,
}: {
  id: string; values: string[]; onChange: (v: string[]) => void; placeholder: string; transform?: (s: string) => string; listId?: string;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const items = raw.split(",").map((s) => transform(s.trim())).filter(Boolean);
    const fresh = items.filter((x, i) => items.indexOf(x) === i && !values.some((v) => v.toLowerCase() === x.toLowerCase()));
    if (fresh.length) onChange([...values, ...fresh]);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === "Enter" || e.key === ",") && draft.trim()) {
      e.preventDefault();
      add(draft);
    } else if (e.key === "Backspace" && !draft && values.length) {
      onChange(values.slice(0, -1));
    }
  };
  return (
    <div className={styles.chips}>
      {values.map((v) => (
        <span key={v} className={styles.chip}>
          {v}
          <button type="button" className={styles.chipX} aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
            ×
          </button>
        </span>
      ))}
      <input
        id={id}
        list={listId}
        className={styles.chipInput}
        value={draft}
        placeholder={values.length ? "" : placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={() => draft.trim() && add(draft)}
      />
    </div>
  );
}

export function ProfileEditor({ initial, version, savedAt }: Props) {
  const [profile, setProfile] = useState<Profile>(initial);
  const [nums, setNums] = useState<Record<NumKey, string>>({
    graduationYear: initial.graduationYear?.toString() ?? "",
    experienceYears: initial.experienceYears?.toString() ?? "",
    minPayLpa: initial.minPayLpa?.toString() ?? "",
  });
  const [fromResume, setFromResume] = useState<Set<keyof Profile>>(new Set());
  const [notice, setNotice] = useState<{ text: string; bad: boolean; link?: { href: string; label: string } } | null>(null);
  const [errors, setErrors] = useState<Partial<Record<keyof Profile, string>>>({});
  const [saved, setSaved] = useState<{ version: number | null; savedAt: string | null }>({ version, savedAt });
  const [dirty, setDirty] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [reading, startReading] = useTransition();
  const [saving, startSaving] = useTransition();
  const fileRef = useRef<HTMLInputElement>(null);

  const set = <K extends keyof Profile>(key: K, value: Profile[K]) => {
    setProfile((p) => ({ ...p, [key]: value }));
    setDirty(true);
  };
  const setList = (key: ListKey) => (v: string[]) => set(key, v as Profile[ListKey]);

  function upload(file: File | undefined) {
    if (!file) return;
    if (file.type && file.type !== "application/pdf") {
      setNotice({ text: "That file is not a PDF. Export your résumé as PDF and try again.", bad: true });
      return;
    }
    const form = new FormData();
    form.set("resume", file);
    setNotice(null);
    startReading(async () => {
      const result = await readResume(form);
      if (!result.ok) {
        setNotice({ text: result.error, bad: true });
        return;
      }
      const { next, filled } = mergeResume(profile, result.fields);
      setProfile(next);
      setNums({
        graduationYear: next.graduationYear?.toString() ?? "",
        experienceYears: next.experienceYears?.toString() ?? "",
        minPayLpa: next.minPayLpa?.toString() ?? "",
      });
      setFromResume(new Set(filled));
      if (filled.length) setDirty(true);
      const left = result.dropped > 0 ? ` ${result.dropped} suggested value${result.dropped === 1 ? " was" : "s were"} left out because ${result.dropped === 1 ? "it is" : "they are"} not in your résumé.` : "";
      setNotice({
        text: filled.length
          ? `Filled ${filled.length} field${filled.length === 1 ? "" : "s"} from your résumé.${left} Check them, then save.`
          : `Nothing new found in your résumé.${left}`,
        bad: false,
      });
    });
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    upload(e.dataTransfer.files?.[0]);
  }

  function save() {
    const toNum = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));
    const candidate = {
      ...profile,
      graduationYear: toNum(nums.graduationYear),
      experienceYears: toNum(nums.experienceYears),
      minPayLpa: toNum(nums.minPayLpa),
    };
    const parsed = ProfileSchema.safeParse(candidate);
    if (!parsed.success) {
      const errs: Partial<Record<keyof Profile, string>> = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof Profile;
        errs[key] ??= key === "graduationYear" ? "Enter a year such as 2026." : key === "workCountries" ? "Use two-letter country codes, such as IN or US." : "Enter a number (or leave it empty).";
      }
      setErrors(errs);
      setNotice({ text: "Some fields need a fix before saving.", bad: true });
      return;
    }
    setErrors({});
    startSaving(async () => {
      const result = await saveProfileAction(parsed.data);
      if (!result.ok) {
        setNotice({ text: result.error, bad: true });
        return;
      }
      setSaved({ version: result.version, savedAt: new Date().toISOString() });
      setDirty(false);
      setFromResume(new Set());
      setNotice({ text: `Saved as version ${result.version}.`, bad: false, link: { href: "/jobs", label: "See the jobs ranked for you →" } });
    });
  }

  const fromCv = (k: keyof Profile, label = "from résumé") => (fromResume.has(k) ? <span className={styles.badge}>{label}</span> : null);
  const savedLine = saved.version
    ? `Version ${saved.version} · saved ${new Date(saved.savedAt ?? "").toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" })}`
    : "Not saved yet";

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.title}>Profile</h1>
        <span className={styles.version}>{savedLine}</span>
      </div>
      <p className={styles.lead}>What you are looking for. Job matching will use it to rank the feed once it is switched on.</p>

      <div
        className={`${styles.drop} ${dragging ? styles.dropActive : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span className={styles.dropIcon} aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M12 18v-6" /><path d="m9 15 3-3 3 3" />
          </svg>
        </span>
        <div>
          <p className={styles.dropTitle}>{reading ? "Reading your résumé…" : "Fill this in from your résumé"}</p>
          <p className={styles.dropNote}>
            Drop a PDF here (up to 4 MB). It is read once and not stored; its text goes only to Groq or NVIDIA.
          </p>
        </div>
        <div>
          <input
            ref={fileRef}
            className={styles.fileInput}
            type="file"
            accept="application/pdf"
            aria-label="Choose résumé PDF"
            onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }}
            disabled={reading}
          />
          <button type="button" className={styles.outline} onClick={() => fileRef.current?.click()} disabled={reading}>
            {reading ? "Reading…" : "Choose PDF"}
          </button>
        </div>
      </div>

      {notice ? (
        <p role={notice.bad ? "alert" : "status"} className={`${styles.notice} ${notice.bad ? styles.noticeBad : ""}`}>
          {notice.text}{notice.link ? <>{" "}<a href={notice.link.href}>{notice.link.label}</a></> : null}
        </p>
      ) : null}

      <section className={styles.card} aria-labelledby="about">
        <h2 id="about" className={styles.cardTitle}>About you</h2>
        <div className={styles.grid}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="name">Name {fromCv("name")}</label>
            <input id="name" className={styles.input} value={profile.name} onChange={(e) => set("name", e.target.value)} autoComplete="name" />
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="headline">One-line summary {fromCv("headline")}</label>
            <input id="headline" className={styles.input} value={profile.headline} placeholder="Final-year B.Tech, Computer Science" onChange={(e) => set("headline", e.target.value)} />
          </div>
          <div className={`${styles.field} ${styles.full}`}>
            <label className={styles.label} htmlFor="education">Education {fromCv("education")}</label>
            <input id="education" className={styles.input} value={profile.education} onChange={(e) => set("education", e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="graduationYear">Graduation year {fromCv("graduationYear")}</label>
            <input id="graduationYear" className={styles.input} inputMode="numeric" value={nums.graduationYear} onChange={(e) => { setNums((n) => ({ ...n, graduationYear: e.target.value })); setDirty(true); }} />
            {errors.graduationYear ? <span className={styles.fieldError}>{errors.graduationYear}</span> : null}
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="experienceYears">Full-time experience (years) {fromCv("experienceYears")}</label>
            <input id="experienceYears" className={styles.input} inputMode="decimal" value={nums.experienceYears} placeholder="0 for freshers" onChange={(e) => { setNums((n) => ({ ...n, experienceYears: e.target.value })); setDirty(true); }} />
            {errors.experienceYears ? <span className={styles.fieldError}>{errors.experienceYears}</span> : null}
          </div>
        </div>
      </section>

      <section className={styles.card} aria-labelledby="wants">
        <h2 id="wants" className={styles.cardTitle}>What you want</h2>
        <div className={styles.grid}>
          <div className={`${styles.field} ${styles.full}`}>
            <label className={styles.label} htmlFor="targetRoles">Target roles {fromCv("targetRoles", "suggested from résumé")}</label>
            <ChipsInput id="targetRoles" values={profile.targetRoles} onChange={setList("targetRoles")} placeholder="e.g. Data Analyst, Business Analyst" />
            <span className={styles.hint}>Press Enter or comma after each one.</span>
          </div>
          <div className={`${styles.field} ${styles.full}`}>
            <span className={styles.label} id="families-label">Fields you want to work in</span>
            <div className={styles.famRow} role="group" aria-labelledby="families-label">
              {ROLE_FAMILIES.map((f) => {
                const on = profile.targetFamilies.includes(f.id);
                return (
                  <button key={f.id} type="button" className={`${styles.fam} ${on ? styles.famOn : ""}`} aria-pressed={on}
                    onClick={() => set("targetFamilies", on ? profile.targetFamilies.filter((x) => x !== f.id) : [...profile.targetFamilies, f.id])}>
                    {f.label}
                  </button>
                );
              })}
            </div>
            <span className={styles.hint}>Jobs in these fields rank higher even when the title is not one of your target roles. Technical or not, pick what fits you.</span>
          </div>
          <div className={`${styles.field} ${styles.full}`}>
            <label className={styles.label} htmlFor="locations">Preferred locations {fromCv("locations")}</label>
            <ChipsInput id="locations" values={profile.locations} onChange={setList("locations")} placeholder="e.g. Bengaluru, Pune" />
          </div>
          <div className={styles.field}>
            <span className={styles.label}>Remote work</span>
            <FilterToggle label="Open to remote roles" pressed={profile.remoteOk} onPressedChange={(on) => set("remoteOk", on)} />
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="minPayLpa">Minimum pay (₹ lakh per year)</label>
            <input id="minPayLpa" className={styles.input} inputMode="decimal" value={nums.minPayLpa} placeholder="Leave empty for no minimum" onChange={(e) => { setNums((n) => ({ ...n, minPayLpa: e.target.value })); setDirty(true); }} />
            {errors.minPayLpa ? <span className={styles.fieldError}>{errors.minPayLpa}</span> : null}
          </div>
        </div>
      </section>

      <section className={styles.card} aria-labelledby="skills">
        <h2 id="skills" className={styles.cardTitle}>Skills {fromCv("skills")}</h2>
        <ChipsInput id="skillsInput" values={profile.skills} onChange={setList("skills")} listId="skill-suggestions" placeholder="e.g. SQL, Python, Excel, Power BI" />
        <datalist id="skill-suggestions">{SKILLS.map((k) => <option key={k.id} value={k.label} />)}</datalist>
        <span className={styles.hint}>
          {profile.skills.length > 0
            ? `${skillIdsFromText(profile.skills).length} of ${profile.skills.length} are skills Hunterrr recognises in job postings; the others still count when a posting names them in its text.`
            : "Start typing: suggestions come from the skills job postings ask for."}
        </span>
      </section>

      <section className={styles.card} aria-labelledby="auth">
        <h2 id="auth" className={styles.cardTitle}>Work authorisation</h2>
        <div className={styles.grid}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="workCountries">Countries you can work in</label>
            <ChipsInput id="workCountries" values={profile.workCountries} onChange={setList("workCountries")} placeholder="IN" transform={(s) => s.toUpperCase().slice(0, 2)} />
            <span className={styles.hint}>Two-letter codes: IN for India, US for the United States.</span>
            {errors.workCountries ? <span className={styles.fieldError}>{errors.workCountries}</span> : null}
          </div>
          <div className={styles.field}>
            <span className={styles.label}>Visa sponsorship</span>
            <FilterToggle label="I need visa sponsorship" pressed={profile.needsSponsorship} onPressedChange={(on) => set("needsSponsorship", on)} />
          </div>
        </div>
      </section>

      <div className={styles.saveBar}>
        <span className={styles.saveStatus} aria-live="polite">{dirty ? "Unsaved changes" : saved.version ? "All changes saved" : "Fill in what you know, then save"}</span>
        <button type="button" className={styles.save} onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save profile"}
        </button>
      </div>
    </div>
  );
}
