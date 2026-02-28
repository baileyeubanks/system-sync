# F012: Cron Monitoring — Stuck Event Detection

## Status: ALREADY IMPLEMENTED (Feb 28, 2026)

The F013 patch added `checkEventPipeline()` to `uptimeMonitor.js`.

### What It Does
- Queries Supabase events table for events stuck > 10 minutes (status = 'pending' or 'processing')
- Queries retry queue for events with retry_count > 3
- Reports stuck count and retry queue depth
- Alerts via Telegram if issues found

### Location
`acs-website/netlify/functions/uptimeMonitor.js` — `checkEventPipeline()` function

### No Further Action Needed
