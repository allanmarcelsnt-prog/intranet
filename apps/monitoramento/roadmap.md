# Requested changes
- [x] Administrator chooses export/archive folder, including PDF and CSV.
- [x] Save a complete archive copy before starting one-calendar-month retention; automatic daily deletion only of eligible archived records.
- [x] Administrator approves new operators and removes former employees' access without losing history.
- [x] Verify rules and workflows (13 tests, authenticated export flows, SQL expiry checks; visual inspection unavailable).
- [x] Export project to GitHub (repository `lthinds/shift-guardian`).
- [x] Run on the company intranet / TrueNAS without Lovable Cloud (Docker stack in `deploy/`, tested: signup → approval → RLS roles, archive + purge, operator removal, backup/restore, HTTPS).
- [x] importador dos dados de produção (deploy/legacy)
- [ ] Weekly report PDF at parity with the current app (the PDF here is plain text via jsPDF).