import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Bricolage_Grotesque, Hanken_Grotesk, DM_Mono } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/SiteHeader";

const geistSans = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

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
  description: "Remote job market analytics dashboard",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F2F6F6" },
    { media: "(prefers-color-scheme: dark)", color: "#0B1516" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${displayFont.variable} ${bodyFont.variable} ${monoFont.variable}`}>
      <body className="min-h-screen bg-background font-sans antialiased flex flex-col">
        <SiteHeader />
        <div className="flex-1">{children}</div>
        {/*
          Remote OK's public API terms (see NOTICE) require a visible attribution
          link on every page, with follow and without nofollow, as a condition of
          API access - not optional styling. This footer is in the root layout so
          it renders on every route without each page having to remember it.
        */}
        <footer className="border-t py-4 px-4 text-center text-xs text-muted-foreground">
          Job data from{" "}
          <a
            href="https://remoteok.com"
            target="_blank"
            rel="noopener"
            className="underline hover:text-foreground"
          >
            Remote OK
          </a>
          , Jobicy, and Himalayas.
        </footer>
      </body>
    </html>
  );
}