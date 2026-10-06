import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Hanken_Grotesk, DM_Mono } from "next/font/google";
import "./globals.css";

// Atlas fonts: display (logo, titles, big numbers), body/UI, and mono
// (scores, pay, dates, small labels). No external stylesheet link.
const displayFont = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["700", "800"],
  variable: "--font-display",
});

const bodyFont = Hanken_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-body",
});

const monoFont = DM_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-dm-mono",
});

export const metadata: Metadata = {
  title: "Hunterrr",
  description: "A personal job-hunting tool: entry-level jobs you can apply to, and an application tracker",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F2F6F6" },
    { media: "(prefers-color-scheme: dark)", color: "#0B1516" },
  ],
};

/**
 * The bare document. Every screen draws its own frame: the signed-in shell (top bar and the
 * Remote OK attribution footer its API terms require, see NOTICE) or the sign-in frame.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${displayFont.variable} ${bodyFont.variable} ${monoFont.variable}`}>
      <body className="min-h-screen bg-background font-sans antialiased">{children}</body>
    </html>
  );
}
