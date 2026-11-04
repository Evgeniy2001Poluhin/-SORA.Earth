/**
 * Compact JSON artifact from Playwright's JSON report.
 *
 * Run with Node's own type stripping -- no ts-node, nothing outside
 * package-lock.json:
 *
 *   node --experimental-strip-types generate-report.ts
 *
 * Exits non-zero when the results file is missing or holds no tests, so a run
 * that produced nothing cannot be reported as an empty success.
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { execSync } from 'node:child_process';

interface PwResult { status: string; duration: number; error?: { message?: string } }
interface PwTest { status: string; results: PwResult[] }
interface PwSpec { title: string; file: string; tests: PwTest[] }
interface PwSuite { title: string; specs?: PwSpec[]; suites?: PwSuite[] }
interface PwReport { suites: PwSuite[] }

interface Scenario { file: string; name: string; status: string; duration_ms: number; error?: string }

function commitSha(): string {
  try {
    return execSync('git rev-parse HEAD', { encoding: 'utf-8' }).trim();
  } catch {
    return 'unknown';
  }
}

function playwrightVersion(): string {
  // The installed version, not the range in package.json.
  const path = 'node_modules/@playwright/test/package.json';
  return existsSync(path) ? JSON.parse(readFileSync(path, 'utf-8')).version : 'unknown';
}

function collect(suite: PwSuite, out: Scenario[]): void {
  for (const spec of suite.specs ?? []) {
    for (const t of spec.tests) {
      const last = t.results[t.results.length - 1];
      out.push({
        file: spec.file,
        name: spec.title,
        // The final attempt's status: passed, failed, timedOut, skipped, interrupted.
        status: last ? last.status : 'skipped',
        duration_ms: t.results.reduce((sum, r) => sum + r.duration, 0),
        error: last?.error?.message,
      });
    }
  }
  for (const child of suite.suites ?? []) collect(child, out);
}

function main(): void {
  const reportPath = 'test-results/results.json';
  if (!existsSync(reportPath)) {
    console.error(`Playwright JSON report not found at ${reportPath}: the tests produced no results.`);
    process.exit(1);
  }
  const raw = JSON.parse(readFileSync(reportPath, 'utf-8')) as PwReport;
  const scenarios: Scenario[] = [];
  for (const suite of raw.suites) collect(suite, scenarios);
  if (scenarios.length === 0) {
    console.error(`${reportPath} holds no tests.`);
    process.exit(1);
  }

  const count = (s: string) => scenarios.filter((x) => x.status === s).length;
  const summary = {
    total: scenarios.length,
    passed: count('passed'),
    failed: scenarios.length - count('passed') - count('skipped'),
    skipped: count('skipped'),
  };
  const artifact = {
    timestamp: new Date().toISOString(),
    commit_sha: commitSha(),
    node_version: process.version,
    playwright_version: playwrightVersion(),
    scenarios,
    summary,
  };
  writeFileSync('e2e-ownership-report.json', JSON.stringify(artifact, null, 2));
  console.log('E2E ownership report: e2e-ownership-report.json');
  console.log(`  Total: ${summary.total}, Passed: ${summary.passed}, Failed: ${summary.failed}, Skipped: ${summary.skipped}`);
}

main();
