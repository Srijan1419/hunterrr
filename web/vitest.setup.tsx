import "@testing-library/jest-dom";
import { configure } from "@testing-library/react";
import { vi } from "vitest";
import React from "react";

// findBy*/waitFor give up after 1 s by default, which is too tight when ~40 test files run in
// parallel (a Save-button test failed once only under full-suite load). Tests that pass wait no longer.
configure({ asyncUtilTimeout: 5000 });

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

// Mock next/image
vi.mock("next/image", () => ({
  default: (props: React.ImgHTMLAttributes<HTMLImageElement> & { src: string; alt: string }) => {
    // eslint-disable-next-line @next/next/no-img-element
    return <img {...props} />;
  },
}));

// Mock lucide-react icons
vi.mock("lucide-react", () => {
  const icons = {};
  return {
    ...icons,
    __esModule: true,
  };
});