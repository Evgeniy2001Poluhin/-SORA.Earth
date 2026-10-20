import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithQuery } from "@/test/utils";

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
