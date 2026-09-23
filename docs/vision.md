# Vision

## Goal

This app lets anyone see their own personal history of races. You run your own
**instance**: it holds one athlete's races and nothing else.

You can run an instance in two ways:

- **Private.** On your own machine or a self-hosted server, for looking at your races
  yourself. Every page needs a login.
- **Public.** On your own domain, so anyone can browse your race history. Visitors read
  without logging in; adding and editing races is reserved for you, the owner.

The core experience is the same in both: a timeline of past races, each one opening into
a detail view that compares official splits with what the watch recorded, shows the
course map and pacing, and records weather, fueling and gear.

## Principles

- **One owner per instance.** No user accounts, no tenancy, no sharing between accounts.
  Someone else who wants the app runs their own instance. This is the main simplification
  of the rewrite: v1 was multi-tenant by design, and every query paid for it.
- **Logging in is only needed to write.** In both modes, every write needs the owner's login. The only
  difference between the modes is whether *reading* needs it too. See
  [access-and-visibility](features/access-and-visibility.md).
- **Few dependencies, each one chosen on purpose.** Python backend, plain HTML + CSS + JS
  frontend with no build step. A frontend library is only added when it does something hard,
  like Leaflet for maps or Chart.js for charts. The backend may use a standard framework
  stack. See [architecture](architecture.md#dependency-policy).
- **FIT files are the source of truth** for recorded data; official results are typed in
  by hand. Strava is secondary and deferred.
- **Partial data is normal.** A race with just a name and a date is a valid race.
  Unknown values show as "—", never as zero.
- **Publishable.** The code, the docs and the test fixtures must be safe to publish. No
  real personal recordings in the repository.

## Audiences

| Who | Mode | Wants |
|---|---|---|
| The owner, at home | private | browse and analyse their own races; import after every race |
| The owner, on their domain | public | the same, plus a presentable public history |
| A visitor | public | browse the owner's public races, read race reports, compare two races |
| Someone self-hosting it | either | clone, set a few env vars, `docker compose up`, create the owner account |

## Non-goals

- Multiple athletes on one instance, teams, or clubs.
- Registration, password-reset emails, account management beyond one owner.
- Training logs. This is a *race* history; the data model and UI assume races.
- A native mobile app. The site should work on a phone; it does not need to be an app.
- A single-page app framework, a JS build step, or a CSS framework.

## Decision log

Decisions for the rewrite, newest last. Feature files hold the detail.

| Decision | Choice | Replaces (v1) |
|---|---|---|
| Owners per instance | exactly one | multi-tenant by design, three ownership roots |
| Site modes | `private` and `public`, one env var | login for everything |
| Edit access | owner login, always, in both modes | — |
| Per-race visibility | `is_public` flag, off by default; only matters in public mode | — |
| Database | PostgreSQL, always, including local runs | PostgreSQL |
| Frontend | plain HTML/CSS/JS, ES modules, no build step | React + Vite + TS + Tailwind + TanStack Query |
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic, kept | same |
| Maps | Leaflet, vendored | react-leaflet |
| Link previews | server-filled `<title>` + Open Graph tags on public pages | — |
| Charts | Chart.js, vendored | Recharts |
| Test fixtures | synthetic FIT files written with `fit-tool` (dev-only); real files only locally, gitignored | real unanonymized files committed, repo kept private |
| Publishing | repository goes public after the rewrite, from clean history | repo must stay private |
| Classification | inferred from measured distances; manual override kept alongside | same |
| Fueling, gear, weather, comparison | kept | same |
