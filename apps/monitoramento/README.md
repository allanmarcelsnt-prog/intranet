# Welcome to your Lovable project

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Open your project in the [Lovable editor](https://lovable.dev) and keep building.

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: connect the project to GitHub and every change made in Lovable is committed straight to your repository.
- **Full ownership**: this code is yours. Push to your repository and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```

## Built with

- TanStack Start
- TypeScript
- React
- Tailwind CSS

## Safenet: archives and access

- New operator registrations are pending until an administrator authorizes them in **Operadores**. Existing accounts were preserved as approved during migration.
- Removing an operator blocks access through database policies immediately, then soft-deletes the sign-in account via the authenticated administration function. The historical profile remains.
- Administrators select a local or accessible network folder in **Relatório semanal → Escolher pasta**. Chrome/Edge requires HTTPS (or localhost) and explicit browser permission. Selection lasts for the current browser session; other browsers use download/save dialogs.
- **Salvar e arquivar mês** saves a complete JSON snapshot for all clients in the month. CSV and PDF are separate human-readable exports. Without a browser-confirmed write, the administrator must confirm the downloaded JSON exists before archiving.
- Retention is one calendar month from the saved archive confirmation, not from the date of the event. A daily 03:00 UTC cleanup may delete eligible records up to 24 hours later. Local files are never deleted by the application; legacy archives without `archived_at` remain protected. Restore and save them again to opt into retention.
- Concurrently changed records do not enter the archive; editing an archived event/bypass returns it to the unarchived state. Active bypasses never expire.

### Intranet deployment (TrueNAS / Docker)

This repository runs **without Lovable Cloud**: `docker-compose.yml` brings up a lean self-hosted Supabase stack (Postgres, GoTrue, PostgREST) behind nginx, plus the app. Full step-by-step guide (install, first admin, HTTPS, backup/restore, updates): **[`deploy/README.md`](deploy/README.md)**.

```sh
./deploy/setup.sh http://<server-ip>:8080   # generates .env with random secrets and JWT keys
docker compose up -d --build                # builds the app, applies drizzle/migrations, starts everything
./deploy/scripts/make-admin.sh you@company  # after signing up in the UI: promotes the first administrator
```

Notes:

- Sign-in is e-mail/password only (the Lovable Google broker was removed). There is no mail server: access is controlled by administrator approval.
- The daily archive cleanup is scheduled automatically with `pg_cron` by the `migrate` service (`deploy/db/post/002-purge-schedule.sql`); `supabase/archive-cleanup-schedule.sql` is kept for reference.
- "Escolher pasta" needs HTTPS with a trusted certificate (see the HTTPS section of the deploy guide).
- Local development without Docker still works with `bun run dev` and `VITE_SUPABASE_URL`/`VITE_SUPABASE_PUBLISHABLE_KEY` pointing at a running stack.
