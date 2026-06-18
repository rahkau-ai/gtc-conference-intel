# Stage 7 — Distribution Checklist

Complete every item before announcing the report.

## Pre-distribution (FM-28, FM-29)

- [ ] PDF filename includes version: `GTC-<Conference>-<Year>-Report-v1.0.pdf`
- [ ] PDF size > 500KB (verified by render.mjs)
- [ ] PDF uploaded to Google Drive (correct folder)
- [ ] Google Drive permissions: "Anyone with link can view" — **verify this before sharing**
- [ ] `DRIVE_FILE_<CONFERENCE>_<YEAR>` env var set in Netlify dashboard
- [ ] Smoke test: click the download link → verify PDF opens correctly
- [ ] Run a second smoke test from an incognito / private browsing window (tests public access)

## Form + email (FM-30)

- [ ] Gated download form live at correct URL
- [ ] Submit test form with a real email address you control
- [ ] Verify confirmation email arrives within 5 minutes
- [ ] Verify lead captured in CRM / Supabase `report_downloads` table

## Announcement

- [ ] LinkedIn post drafted and approved
- [ ] Newsletter mention (if timed to send)
- [ ] Website report page updated with new version

## Post-distribution log

Record here when done:
- Announced: <!-- date and channels -->
- Drive file ID: <!-- for DRIVE_FILE env var -->
- Download URL: <!-- public link -->
- Version: <!-- e.g. v1.0 -->
- Signed off by: <!-- name -->
