"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { FilterToggle } from "@/components/atlas/FilterToggle";
import styles from "./feed.module.css";

const COUNTRIES: { code: string; label: string }[] = [
  { code: "any", label: "Any country" },
  { code: "IN", label: "India" },
  { code: "US", label: "United States" },
  { code: "GB", label: "United Kingdom" },
  { code: "CA", label: "Canada" },
  { code: "DE", label: "Germany" },
];

/** URL-driven filters: every change rewrites the query string and resets to page 1. */
export function FeedFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const urlQ = params.get("q") ?? "";
  const [q, setQ] = useState(urlQ);
  useEffect(() => setQ(urlQ), [urlQ]); // Back/Forward and cleared filters keep the box honest
  // Defaults: open to India (no param), "any" turns it off. Remote is on unless remote=0.
  const rawCountry = (params.get("country") ?? "").trim();
  const country = rawCountry.toLowerCase() === "any" ? "any" : rawCountry === "" ? "IN" : rawCountry.toUpperCase();
  const options = COUNTRIES.some((c) => c.code === country)
    ? COUNTRIES
    : [...COUNTRIES, { code: country, label: country }];
  const remoteOn = params.get("remote") !== "0";

  function push(mutate: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(params.toString());
    mutate(next);
    next.delete("page");
    const qs = next.toString();
    router.replace(qs ? `?${qs}` : "?", { scroll: false });
  }

  function setFlag(key: string, on: boolean, value = "1") {
    push((n) => (on ? n.set(key, value) : n.delete(key)));
  }

  // Anything that narrows the list beyond the defaults (entry level on, newest-or-best sorting).
  const active =
    ["q", "pay", "days"].some((k) => params.get(k)) ||
    params.get("level") === "all" ||
    params.get("remote") === "0" ||
    (rawCountry !== "" && rawCountry.toUpperCase() !== "IN");

  function submit(e: FormEvent) {
    e.preventDefault();
    push((n) => (q.trim() ? n.set("q", q.trim()) : n.delete("q")));
  }

  return (
    <form className={styles.filters} onSubmit={submit} role="search" aria-label="Filter jobs">
      <input
        className={styles.search}
        type="search"
        name="q"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search title or company"
        aria-label="Search title or company"
      />
      <FilterToggle
        label="Entry level"
        pressed={params.get("level") !== "all"}
        onPressedChange={(on) => push((n) => (on ? n.delete("level") : n.set("level", "all")))}
      />
      <FilterToggle
        label="Remote"
        pressed={remoteOn}
        onPressedChange={(on) => push((n) => (on ? n.delete("remote") : n.set("remote", "0")))}
      />
      <FilterToggle label="Pay stated" pressed={params.get("pay") === "1"} onPressedChange={(on) => setFlag("pay", on)} />
      <FilterToggle label="Last 7 days" pressed={params.get("days") === "7"} onPressedChange={(on) => setFlag("days", on, "7")} />
      <select
        className={styles.select}
        aria-label="Eligible country"
        value={country}
        onChange={(e) => push((n) => (e.target.value === "IN" ? n.delete("country") : n.set("country", e.target.value)))}
      >
        {options.map((c) => (
          <option key={c.code} value={c.code}>
            {c.label}
          </option>
        ))}
      </select>
      {active ? (
        <button type="button" className={styles.clear} onClick={() => router.replace("?", { scroll: false })}>
          Clear filters
        </button>
      ) : null}
    </form>
  );
}
