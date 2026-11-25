# SlowBurnBotGamma

## Project Overview

Full-stack bot management platform:
- **Backend:** FastAPI + SQLAlchemy 2.0 async + Alembic migrations, PostgreSQL on Railway
- **Frontend:** Next.js (App Router), deployed on Railway
- **Bot client:** Python (`bot-client/`), versioned via `bot-client/burnBot_version.py`
- **Billing:** Stripe subscriptions, three tiers: crawl / walk / run
- **CI/CD:** GitHub Actions for bot build dispatch and artifact download
- **Deployment:** Railway (backend service + frontend service + PostgreSQL)

Key conventions:
- `APP_VERSION` is bumped by `scripts/gpush.sh` — use it in place of `git push` (it bumps, commits, then pushes with the bump skipped on that push); never bump manually. `githooks/pre-push` is a no-op stub that just points to `gpush.sh`.
- `BOT_VERSION` in `bot-client/burnBot_version.py` must be incremented with every bot-client change
- The `SlowBurnBotGamma` backend service builds via **Railpack** on Railway, not the root `Dockerfile` — that file's `alembic upgrade head && uvicorn ...` CMD is not what actually runs in production, and is easy to mistake for the deploy path. Migrations run automatically via Railway's **pre-deploy command** (`alembic upgrade head`, set on the service, applies before the new version takes traffic — fails closed, previous version keeps running if it fails). Alembic migrations still need a revision file added to `alembic/versions/`; the pre-deploy command just means you no longer have to run `alembic upgrade head` manually after pushing one.
- Admin account is system-level only — never link to customer-facing pages
- Use `----` for empty/unset fields in UI, `****` for set secrets

## Pointers

- Frontend has its own scoped doc: `frontend/AGENTS.md`
- Railway + GitHub plugins are enabled in `.claude/settings.json`
- Deploy/release scripts live in `scripts/` (`deploy.sh`, `release-bot-client.sh`, `bump-frontend-version.mjs`)
- Tests: `.venv/bin/python -m unittest discover -s tests -t .` (Selenium is stubbed in `tests/_stubs.py`). They cover the seed pool logic and the seed rules of the backend.
- Many files use CRLF line endings (`app/routers/*.py`, `bot-client/*.py`, some frontend files); others use LF. Edit them with the Edit tool. A Python rewrite in text mode changes every line ending: check with `grep -c $'\r' <file>` before a scripted edit.
- Never modify secrets, credentials, or deployment configs without explicit confirmation
