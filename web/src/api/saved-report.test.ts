import { describe, it, expect, vi } from "vitest";
import { reportApi } from "./endpoints/report";
import { auth } from "./client";
import { session } from "@/lib/session";

// The saved report endpoint always uses the real authenticated transport.
describe("saved PDF transport", () => {
  it("uses saved ID with Bearer instead of project inputs", async () => {
    auth.set("saved-pdf-token");
    const fetch = vi.fn(async () => new Response("%PDF-1.4 saved", {status:200}));
    vi.stubGlobal("fetch", fetch);
    const blob = await reportApi.savedPdf(42);
    expect(await blob.text()).toContain("saved");
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/history/42/report.pdf");
    expect(init.method).toBe("GET");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer saved-pdf-token");
    expect(init.body).toBeUndefined();
  });
  it("rejects a PDF body that completes after account transition", async () => {
    let resolve!: (b: Blob) => void;
    const body = new Promise<Blob>(r => {resolve=r;});
    const reading = vi.fn(() => body);
    vi.stubGlobal("fetch", vi.fn(async () => ({ok:true,status:200,blob:reading})));
    const pending = reportApi.savedPdf(42);
    const assertion = expect(pending).rejects.toThrow("session changed");
    await vi.waitFor(() => expect(reading).toHaveBeenCalled());
    session.advance(); resolve(new Blob(["previous user's PDF"]));
    await assertion;
  });
  it("rejects invalid IDs without a network request", async () => {
    const fetch=vi.fn(); vi.stubGlobal("fetch",fetch);
    await expect(reportApi.savedPdf(-1)).rejects.toThrow("Invalid evaluation ID");
    expect(fetch).not.toHaveBeenCalled();
  });
});
