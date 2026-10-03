import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { nextTheme, ThemeToggle } from "@/components/shell/ThemeToggle";

function stubMatchMedia(dark: boolean) {
  const stub = vi.fn().mockImplementation((query: string) => ({
    matches: dark && query === "(prefers-color-scheme: dark)",
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  Object.defineProperty(window, "matchMedia", { value: stub, writable: true, configurable: true });
}

const realLocalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  stubMatchMedia(false);
});

afterEach(() => {
  if (realLocalStorage) {
    Object.defineProperty(window, "localStorage", realLocalStorage);
  }
  document.documentElement.removeAttribute("data-theme");
});

describe("nextTheme", () => {
  it("flips dark to light and light to dark", () => {
    expect(nextTheme("dark")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
  });
});

describe("ThemeToggle", () => {
  it("flips the data-theme attribute and renames itself to what it WILL do", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Switch to dark theme" });
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();
  });

  it("remembers the choice in localStorage and starts from it", () => {
    window.localStorage.setItem("theme", "dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("starts from the system preference when nothing is stored", () => {
    stubMatchMedia(true);
    render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
  });

  it("survives a throwing localStorage", () => {
    Object.defineProperty(window, "localStorage", {
      value: {
        getItem: () => {
          throw new Error("blocked");
        },
        setItem: () => {
          throw new Error("blocked");
        },
        removeItem: () => {
          throw new Error("blocked");
        },
        clear: () => {
          throw new Error("blocked");
        },
        length: 0,
        key: () => null,
      },
      configurable: true,
    });
    let button: HTMLElement;
    expect(() => render(<ThemeToggle />)).not.toThrow();
    button = screen.getByRole("button", { name: /switch to (light|dark) theme/i });
    expect(() => fireEvent.click(button)).not.toThrow();
    expect(document.documentElement.dataset.theme).toMatch(/^(light|dark)$/);
  });
});

describe("ThemeToggle sync and storage", () => {
  it("follows data-theme changes made elsewhere (command palette)", async () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeTruthy();
    document.documentElement.dataset.theme = "dark";
    expect(await screen.findByRole("button", { name: "Switch to light theme" })).toBeTruthy();
  });

  it("does not store the system preference until the user chooses", () => {
    stubMatchMedia(true);
    render(<ThemeToggle />);
    expect(window.localStorage.getItem("theme")).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(window.localStorage.getItem("theme")).toBe("light");
  });
});
