/**
 * No new consumer may select a domain branch by matching server prose (#250).
 *
 * This is a ratchet, not a ban: it records the one place that still does it and
 * fails on a second. Writing the rule as "nobody does this" would have been
 * false the day it was written, and a rule that is already violated teaches
 * people to disable it.
 *
 * The space is enumerated structurally rather than by grepping for the phrase
 * that happened to be there. Measured 2026-09-06 across the non-test frontend:
 * nine `.includes` / `.startsWith` / `.match` / `.indexOf` calls in total, and
 * exactly one of them reads a server error's text. `SchedulerPanel.tsx` matches
 * `"OK"` against a string it set two lines earlier -- one file's own
 * convention, not a contract across a process boundary, so it is not this.
 */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** `web/src`, resolved from this file rather than from the working directory,
 *  so the scan covers the same tree whichever way vitest is invoked. */
const SRC = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * Places allowed to still branch on an error's text, and why.
 *
 * `DriftPage.tsx` keeps its substring as a transitional fallback: a freshly
 * loaded bundle talking to a backend that has not been redeployed yet gets no
 * `reason_code` to read. Both branches are covered in
 * `DriftPage.production.test.tsx`, so deleting the fallback later is a one-line
 * change whose test says whether it still mattered.
 */
const ALLOWED = new Set(["features/drift/DriftPage.tsx"]);

function sourceFiles(dir: string, prefix = ""): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const rel = prefix ? `${prefix}/${entry}` : entry;
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full, rel));
    } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && !rel.startsWith("test/")) {
      found.push(rel);
    }
  }
  return found;
}

/** Text matching applied within reach of an error message. */
const MATCHERS = /\.(includes|startsWith|endsWith|match|indexOf|search)\s*\(/;

describe("error branching", () => {
  it("selects domain branches by reason_code, not by the wording of a message", () => {
    const offenders: string[] = [];

    for (const rel of sourceFiles(SRC)) {
      const source = readFileSync(join(SRC, rel), "utf8");
      // Comments talk *about* this pattern in several places, including the
      // fix itself. Matching them would make the guard fail on its own prose.
      const code = source
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");

      for (const found of code.matchAll(/errorMessage\s*\(/g)) {
        const window = code.slice(found.index, found.index + 400);
        if (MATCHERS.test(window) && !ALLOWED.has(rel)) {
          const line = code.slice(0, found.index).split("\n").length;
          offenders.push(`${rel}:${line}`);
        }
      }
    }

    expect(offenders, [
      "These branch on the text of a server error. Text is not a contract:",
      "rewording a sentence, adding a full stop or translating it changes",
      "behaviour with nothing going red. Have the handler return a",
      "`reason_code` and read `ApiError.reasonCode` instead (#250).",
    ].join(" ")).toEqual([]);
  });

  it("finds the allowlisted case, so the scan is known to work", () => {
    // Without this, deleting the scan's own matcher would leave it green: an
    // empty result is what a broken scan and a clean codebase both produce.
    //
    // It also goes red when the transitional fallback is finally deleted, and
    // that is deliberate -- a stale allowance is the same defect as a missing
    // one. The fix then is to drop the entry from ALLOWED and this assertion
    // with it, which is how the guard tightens by one notch on its own.
    const source = readFileSync(join(SRC, "features/drift/DriftPage.tsx"), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

    const hits = [...code.matchAll(/errorMessage\s*\(/g)].filter((m) =>
      MATCHERS.test(code.slice(m.index, m.index + 400)),
    );

    expect(hits.length).toBeGreaterThan(0);
  });

  it("sees every source file it is supposed to", () => {
    // A scan over an empty file list also reports no offenders.
    const files = sourceFiles(SRC);

    expect(files.length).toBeGreaterThan(50);
    expect(files).toContain("features/drift/DriftPage.tsx");
    expect(files).toContain("api/client.ts");
    expect(files.filter((f) => f.includes(".test."))).toEqual([]);
  });
});
