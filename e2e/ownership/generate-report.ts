/**
 * Generate a small JSON report artifact from Playwright results.
 *
 * Runs after tests complete. Reads the JSON report and produces a
 * compact artifact with commit SHA, versions, and per-scenario results.
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { execSync } from 'child_process';

interface PlaywrightResult {
  suites: Array<{
    title: string;
    specs: Array<{
      title: string;
      tests: Array<{
        status: string;
        duration: number;
        error?: { message: string };
      }>;
    }>;
  }>;
}

interface ReportArtifact {
  timestamp: string;
  commit_sha: string;
  node_version: string;
  playwright_version: string;
  scenarios: Array<{
    name: string;
    status: 'passed' | 'failed' | 'skipped';
    duration_ms: number;
    error?: string;
  }>;
  summary: {
    total: number;
    passed: number;
    failed: number;
    skipped: number;
  };
}

function getCommitSha(): string {
  try {
    return execSync('git rev-parse HEAD', { encoding: 'utf-8' }).trim();
  } catch {
    return 'unknown';
  }
}

function getPlaywrightVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync('package.json', 'utf-8'));
    return pkg.devDependencies['@playwright/test'] || 'unknown';
  } catch {
    return 'unknown';
  }
}

function main() {
  const reportPath = 'test-results/results.json';

  if (!existsSync(reportPath)) {
    console.error('Playwright JSON report not found at:', reportPath);
    console.error('Tests did not produce results. Report generation failed.');
    process.exit(1);
  }

  const rawReport: PlaywrightResult = JSON.parse(readFileSync(reportPath, 'utf-8'));

  const scenarios: ReportArtifact['scenarios'] = [];

  for (const suite of rawReport.suites) {
    for (const spec of suite.specs) {
      for (const test of spec.tests) {
        scenarios.push({
          name: spec.title,
          status: test.status as any,
          duration_ms: test.duration,
          error: test.error?.message,
        });
      }
    }
  }

  const summary = {
    total: scenarios.length,
    passed: scenarios.filter(s => s.status === 'passed').length,
    failed: scenarios.filter(s => s.status === 'failed').length,
    skipped: scenarios.filter(s => s.status === 'skipped').length,
  };

  const artifact: ReportArtifact = {
    timestamp: new Date().toISOString(),
    commit_sha: getCommitSha(),
    node_version: process.version,
    playwright_version: getPlaywrightVersion(),
    scenarios,
    summary,
  };

  const outputPath = 'e2e-ownership-report.json';
  writeFileSync(outputPath, JSON.stringify(artifact, null, 2));

  console.log('E2E ownership report generated:', outputPath);
  console.log(`  Total: ${summary.total}, Passed: ${summary.passed}, Failed: ${summary.failed}, Skipped: ${summary.skipped}`);
}

main();
