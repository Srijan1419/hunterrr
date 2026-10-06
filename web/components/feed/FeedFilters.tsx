"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { FilterToggle } from "@/components/atlas/FilterToggle";
import styles from "./feed.module.css";

const COUNTRIES: { code: string; label: string }[] = [
  { code: "", label: "Any country" },
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
  const country = (params.get("country") ?? "").toUpperCase();
  const options = COUNTRIES.some((c) => c.code === country)
    ? COUNTRIES
    : [...COUNTRIES, { code: country, label: country }];

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
      <FilterToggle label="Remote" pressed={params.get("remote") === "1"} onPressedChange={(on) => setFlag("remote", on)} />
      <FilterToggle label="Pay stated" pressed={params.get("pay") === "1"} onPressedChange={(on) => setFlag("pay", on)} />
      <FilterToggle label="Last 7 days" pressed={params.get("days") === "7"} onPressedChange={(on) => setFlag("days", on, "7")} />
      <select
        className={styles.select}
        aria-label="Eligible country"
        value={country}
        onChange={(e) => push((n) => (e.target.value ? n.set("country", e.target.value) : n.delete("country")))}
      >
        {options.map((c) => (
          <option key={c.code} value={c.code}>
            {c.label}
          </option>
        ))}
      </select>
    </form>
  );
}
