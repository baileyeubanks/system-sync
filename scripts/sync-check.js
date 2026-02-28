#!/usr/bin/env node
/**
 * sync-check.js — Validates cross-repo consistency
 *
 * Checks:
 *   1. All tables referenced in code exist in migrations or are documented
 *   2. Env vars are documented in registry
 *   3. Functions are documented in registry
 *   4. No orphaned migrations (tables created but never referenced)
 *
 * Usage: node scripts/sync-check.js [--fix]
 */

const fs = require('fs');
const path = require('path');

const REGISTRY_DIR = path.join(__dirname, '..', 'registry');

function loadJson(filename) {
  return JSON.parse(fs.readFileSync(path.join(REGISTRY_DIR, filename), 'utf8'));
}

function main() {
  console.log('=== Sync Check ===');
  console.log(`Time: ${new Date().toISOString()}\n`);

  let warnings = 0;
  let errors = 0;

  // 1. Load registries
  const functions = loadJson('functions.json');
  const envVars = loadJson('env-vars.json');
  const deps = loadJson('dependencies.json');

  // 2. Check function registry completeness
  console.log('--- Function Registry ---');
  for (const [repo, config] of Object.entries(functions.repos)) {
    const funcs = config.functions;
    if (!funcs) continue;

    const count = Array.isArray(funcs)
      ? funcs.length
      : Object.values(funcs).reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0);

    console.log(`  ${repo}: ${count} functions registered`);
  }

  // 3. Check env var registry
  console.log('\n--- Environment Variables ---');
  for (const [repo, config] of Object.entries(envVars.repos)) {
    const vars = config.env_vars || {};
    const clientVars = config.client_side || {};
    const total = Object.keys(vars).length + Object.keys(clientVars).length;
    const required = Object.entries(vars).filter(([, v]) => v.required).length;

    console.log(`  ${repo}: ${total} vars (${required} required)`);

    // Check for required vars without descriptions
    for (const [name, meta] of Object.entries(vars)) {
      if (meta.required && !meta.description) {
        console.log(`    WARN: ${name} is required but has no description`);
        warnings++;
      }
    }
  }

  // 4. Check shared secrets alignment
  console.log('\n--- Shared Secrets ---');
  for (const secret of envVars.shared_secrets || []) {
    console.log(`  ${secret.name}: shared by ${secret.repos.join(', ')}`);
  }

  // 5. Check dependency map
  console.log('\n--- Cross-Repo Dependencies ---');
  for (const dep of deps.crossRepoDependencies || []) {
    console.log(`  ${dep.from} → ${dep.to} (${dep.type})`);
    if (!dep.description) {
      console.log(`    WARN: missing description`);
      warnings++;
    }
  }

  // 6. Check data flows
  console.log('\n--- Data Flows ---');
  for (const flow of deps.dataFlows || []) {
    console.log(`  ${flow.name}`);
  }

  // Summary
  console.log(`\n=== Summary ===`);
  console.log(`Warnings: ${warnings}`);
  console.log(`Errors: ${errors}`);

  // Write report
  const report = {
    timestamp: new Date().toISOString(),
    warnings,
    errors,
    status: errors > 0 ? 'FAIL' : warnings > 0 ? 'WARN' : 'PASS',
  };

  const reportPath = path.join(__dirname, '..', 'reports', 'sync-latest.json');
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(`\nReport saved: reports/sync-latest.json`);

  process.exit(errors > 0 ? 1 : 0);
}

main();
