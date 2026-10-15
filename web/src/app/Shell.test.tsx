import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// ModelBadge needs a query client, and the tour is a separate concern; neither
// is under test here.
vi.mock("../components/ModelBadge", () => ({ ModelBadge: () => null }));
vi.mock("./OnboardingTour", () => ({ default: () => null }));

import { Shell } from "./Shell";

describe("Shell nav", () => {
  it("renders on a route with no matching link, without throwing", () => {
    expect(() =>
      render(
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
