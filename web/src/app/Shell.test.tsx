import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ADMIN, SPECIALIST_A, renderWithQuery, seedSession } from "@/test/utils";

// ModelBadge is a separate concern; the tour is too. The header pill now reads
// the health query, so the render goes through a query client.
vi.mock("../components/ModelBadge", () => ({ ModelBadge: () => null }));
vi.mock("./OnboardingTour", () => ({ default: () => null }));

import { Shell } from "./Shell";

describe("Shell nav", () => {
  it("renders on a route with no matching link, without throwing", () => {
    expect(() =>
      renderWithQuery(
        <MemoryRouter initialEntries={["/no-such-page"]}>
          <Shell />
        </MemoryRouter>,
      ),
    ).not.toThrow();

    // jsdom has no layout, so this checks only that the nav is all there.
    const nav = screen.getByRole("navigation");
    expect(within(nav).getAllByRole("link")).toHaveLength(14);
  });
});

describe("Shell menu by role", () => {
  const links = () => within(screen.getByRole("navigation")).getAllByRole("link").map((a) => a.textContent);
  const renderShell = () =>
    renderWithQuery(<MemoryRouter initialEntries={["/"]}><Shell /></MemoryRouter>);

  it("hides Co-Pilot from a specialist, and keeps Evaluate and History", () => {
    seedSession(SPECIALIST_A, "tok-a");
    renderShell();
    expect(links()).not.toContain("✨ Co-Pilot");
    expect(links()).toContain("History");
    expect(links()).toContain("Evaluate");
  });

  it("keeps Co-Pilot for an administrator", () => {
    seedSession(ADMIN, "tok-admin");
    renderShell();
    expect(links()).toContain("✨ Co-Pilot");
  });

  it("names the account and offers sign-out and account switch", () => {
    seedSession(SPECIALIST_A, "tok-a");
    renderShell();
    expect(screen.getByText("spec-a · specialist")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch account" })).toBeInTheDocument();
  });

  it("offers sign-in to anonymous", () => {
    renderShell();
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
