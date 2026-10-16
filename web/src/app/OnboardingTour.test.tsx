import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import OnboardingTour from "./OnboardingTour";

const KEY = "sora_tour_done";

// jsdom has no layout. These stubs give the tour a 375 x 812 viewport and place
// the nav links where a test needs them; everything else measures as zero.
type Box = { left: number; top: number; width: number; height: number };
let navBox: Box = { left: 0, top: 0, width: 0, height: 0 };

function setViewport(width: number, height: number) {
  Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, get: () => width });
  Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, get: () => height });
}

beforeEach(() => {
  localStorage.clear();
  setViewport(375, 812);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const b = this.matches("[data-tour]") ? navBox : { left: 0, top: 0, width: 0, height: 0 };
    return {
      ...b,
      x: b.left,
      y: b.top,
      right: b.left + b.width,
      bottom: b.top + b.height,
      toJSON() { return this; },
    } as unknown as DOMRect;
  });
});

afterEach(() => {
  delete (document.documentElement as unknown as Record<string, unknown>).clientWidth;
  delete (document.documentElement as unknown as Record<string, unknown>).clientHeight;
});

function floater(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".__floater");
}

// The tour's targets: Joyride skips a step whose target is not in the DOM.
function Nav() {
  return (
    <nav>
      <a href="/evaluate" data-tour="evaluate">Evaluate</a>
      <a href="/copilot" data-tour="copilot">Co-Pilot</a>
      <a href="/map" data-tour="map">Map</a>
      <a href="/status" data-tour="status">Status</a>
    </nav>
  );
}

function renderTour() {
  return render(
    <>
      <Nav />
      <OnboardingTour />
    </>,
  );
}

// Joyride and popper run timers and layout in jsdom, so each test here takes
// one to two seconds on an idle machine and several times that on a loaded one.
// Vitest's 5 s default then fails them for reasons unrelated to the tour.
const SLOW = 20_000;

describe("OnboardingTour", () => {
  it("Skip sets sora_tour_done, and the tour stays closed on the next visit", async () => {
    const first = render(<OnboardingTour />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await waitFor(() => expect(localStorage.getItem(KEY)).toBe("1"));
    first.unmount();

    render(<OnboardingTour />);
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
  }, SLOW);

  it("moves focus to Next when a step after the welcome card opens", async () => {
    renderTour();
    fireEvent.click(await screen.findByRole("button", { name: /^Next/ }));
    const next = await screen.findByRole("button", { name: /^Next/ });
    await waitFor(() => expect(next).toHaveFocus());
  }, SLOW);

  it("centres a step whose target is off the screen", async () => {
    // Map link: off the right edge of a 375 px screen, inside the nav.
    navBox = { left: 701, top: 85, width: 82, height: 40 };
    renderTour();
    fireEvent.click(await screen.findByRole("button", { name: /^Next/ }));
    await waitFor(() => expect(floater()?.style.position).toBe("fixed"));
  }, SLOW);

  it("keeps a step's own placement when its target is on the screen", async () => {
    // Evaluate link at 375 x 812, as measured in the browser.
    navBox = { left: 88, top: 85, width: 82, height: 40 };
    renderTour();
    fireEvent.click(await screen.findByRole("button", { name: /^Next/ }));
    await waitFor(() => expect(floater()?.style.position).toBe("absolute"));
  }, SLOW);
});
