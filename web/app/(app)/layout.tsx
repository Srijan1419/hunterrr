import type { ReactNode } from "react";
import { AppShell } from "@/components/shell/AppShell";

/** The frame every signed-in screen lives in. */
export default function AppGroupLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
