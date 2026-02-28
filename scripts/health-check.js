#!/usr/bin/env node
/**
 * health-check.js — Checks health of all services in the ACS + CC ecosystem
 *
 * Usage: node scripts/health-check.js [--verbose]
 */

const https = require('https');
const http = require('http');

const VERBOSE = process.argv.includes('--verbose');

const SERVICES = [
  // Websites
  { name: 'ACS Website', url: 'https://astrocleanings.com', expect: 200 },
  { name: 'Content Co-op', url: 'https://contentco-op.com', expect: 200 },
  { name: 'CoDeliver', url: 'https://codeliver.cc', expect: 200 },
  { name: 'CoScript', url: 'https://coscript.cc', expect: 200 },
  { name: 'CoEdit', url: 'https://coedit.cc', expect: 200 },
  { name: 'Portfolio', url: 'https://baileyeubanks.com', expect: 200 },

  // API endpoints
  { name: 'ACS API (slots)', url: 'https://astrocleanings.com/api/getAvailableSlots', expect: 200 },
  { name: 'ACS API (uptime)', url: 'https://astrocleanings.com/api/uptimeMonitor', expect: 200 },

  // Supabase
  { name: 'Supabase', url: 'https://briokwdoonawhxisbydy.supabase.co/rest/v1/', expect: [200, 401] },

  // Mac Mini (Blaze V4) — only reachable from local network
  // Uncomment when running from a machine on the same network as 10.0.0.21
  // { name: 'Blaze OpenClaw', url: 'http://10.0.0.21:18789/health', expect: 200, local: true },
  // { name: 'Blaze FastAPI', url: 'http://10.0.0.21:8899/health', expect: 200, local: true },
];

function checkUrl(service) {
  return new Promise((resolve) => {
    const startTime = Date.now();
    const client = service.url.startsWith('https') ? https : http;

    const req = client.get(service.url, { timeout: 10000 }, (res) => {
      const elapsed = Date.now() - startTime;
      const expects = Array.isArray(service.expect) ? service.expect : [service.expect];
      const ok = expects.includes(res.statusCode);

      // Consume response body
      res.resume();

      resolve({
        name: service.name,
        url: service.url,
        status: res.statusCode,
        elapsed,
        ok,
      });
    });

    req.on('error', (err) => {
      resolve({
        name: service.name,
        url: service.url,
        status: 'ERROR',
        elapsed: Date.now() - startTime,
        ok: false,
        error: err.message,
      });
    });

    req.on('timeout', () => {
      req.destroy();
      resolve({
        name: service.name,
        url: service.url,
        status: 'TIMEOUT',
        elapsed: 10000,
        ok: false,
      });
    });
  });
}

async function main() {
  console.log('=== System Health Check ===');
  console.log(`Time: ${new Date().toISOString()}\n`);

  const results = await Promise.all(SERVICES.map(checkUrl));

  let passing = 0;
  let failing = 0;

  for (const r of results) {
    const icon = r.ok ? '  OK' : 'FAIL';
    const line = `[${icon}] ${r.name.padEnd(25)} ${String(r.status).padEnd(8)} ${r.elapsed}ms`;
    console.log(line);
    if (VERBOSE && r.error) console.log(`       Error: ${r.error}`);
    r.ok ? passing++ : failing++;
  }

  console.log(`\n${passing}/${results.length} passing, ${failing} failing`);

  // Output JSON report
  const report = {
    timestamp: new Date().toISOString(),
    results,
    summary: { total: results.length, passing, failing },
  };

  const fs = require('fs');
  const reportPath = require('path').join(__dirname, '..', 'reports', 'health-latest.json');
  fs.mkdirSync(require('path').dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(`\nReport saved: reports/health-latest.json`);

  process.exit(failing > 0 ? 1 : 0);
}

main();
