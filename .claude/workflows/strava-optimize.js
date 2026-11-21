export const meta = {
  name: 'strava-optimize',
  description: 'Review the Strava integration through four lenses, adversarially verify each finding, fix the confirmed ones, and gate on the full test suite',
  whenToUse: 'After changing anything under backend/app/services/strava*.py, routers/strava.py, the Strava frontend, or docs/features/strava.md. Args: {rounds?: number (default 1, loops until a round confirms nothing), fix?: boolean (default true), focus?: string}',
  phases: [
    { title: 'Review', detail: 'four independent lenses: Strava compliance, security, data correctness, frontend + efficiency' },
    { title: 'Verify', detail: 'one skeptic per lens tries to refute every finding, preferably with a failing test' },
    { title: 'Fix', detail: 'one agent applies all confirmed findings, adding a regression test for each' },
    { title: 'Gate', detail: 'ruff, pytest, node --check, and a completeness critic' },
  ],
}

// ---------------------------------------------------------------------------------------
// The files under review. Reviewers read them in full; everything else only as needed.
const SCOPE = `
Backend: backend/app/services/strava_api.py (HTTP, OAuth, rate limits), backend/app/services/strava_map.py
(activity -> ParsedFile), backend/app/services/strava.py (connection, chains, pull, tagline, purge, webhook),
backend/app/routers/strava.py (OAuth redirects, owner API, webhook), the Strava bits of backend/app/routers/access.py
(login_method, visibility consent), backend/app/services/imports.py (place/Received/parse_upload, shared with FIT),
backend/app/models/strava.py, backend/app/migrations/versions/0005_strava.py, backend/app/cli.py (strava-unlink,
strava-subscribe), backend/tests/test_strava.py.
Frontend: frontend/static/js/strava.js, login.js, site.js, timeline.js, admin-race-edit.js, race.js, types.js,
frontend/static/css/strava.css, tokens.css.
Spec: docs/features/strava.md is the contract. docs/reference/fit-files.md holds the import traps it refers to.`

const STRAVA_FACTS = `
Strava facts the code must respect (from the 2026 developer docs, API Agreement and Policy):
- OAuth code flow; no OIDC: identify by athlete.id. Callback ?scope= is what was actually granted (users can untick).
  Access tokens last 6 h; refresh tokens rotate and the old one is invalid at once: always store the newest.
- Revoke with POST https://www.strava.com/oauth/revoke (Basic auth client id/secret, token, token_type_hint);
  /oauth/deauthorize retires 2027-06-01. Tokens must travel in the Authorization header by 2027-06-01.
- Scopes: activity:read excludes Only-Me activities and privacy-zone data; activity:read_all includes both;
  activity:write needed to PUT /activities/{id} (description replaces the whole text).
- workout_type: run 1 = race, ride 11 = race; swims have no documented race value. No multisport activity type.
- device_watts false = Strava-estimated power. start_date_local ends in Z but is local wall time.
- Rate limits: overall 200/15 min, 2000/day; read 100/15 min, 1000/day (headers X-RateLimit-* and
  X-ReadRateLimit-*, "15min,daily"); 429 when exceeded; 15-min windows reset on the quarter hour, daily at 00:00 UTC.
- Webhook: one subscription per app; GET hub.challenge handshake echoing {"hub.challenge"}; POST events must get
  a 200 within 2 s; events are unsigned; deauth arrives as athlete update with updates.authorized "false".
- Agreement: a user's Strava data may be shown only to that user unless they explicitly consent; delete Strava
  data after revocation (within 30 days); no use of Strava data with AI/ML; "View on Strava" link text, legible
  and clearly a link (bold/underline or #FC5200); official "Connect with Strava" button, 48 px tall; don't imply
  Strava endorses the app.`

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          failure: { type: 'string', description: 'concrete inputs/state -> wrong outcome' },
          fix: { type: 'string', description: 'the smallest change that fixes it' },
        },
        required: ['title', 'file', 'line', 'severity', 'failure', 'fix'],
      },
    },
  },
  required: ['findings'],
}

const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'integer' },
          real: { type: 'boolean' },
          evidence: { type: 'string', description: 'the test you ran, or the code path that refutes/confirms' },
        },
        required: ['index', 'real', 'evidence'],
      },
    },
  },
  required: ['verdicts'],
}

const FIXED = {
  type: 'object',
  properties: {
    fixed: { type: 'array', items: { type: 'string' } },
    skipped: { type: 'array', items: { type: 'string' }, description: 'title: why' },
    tests_added: { type: 'array', items: { type: 'string' } },
  },
  required: ['fixed', 'skipped', 'tests_added'],
}

const GATE = {
  type: 'object',
  properties: {
    green: { type: 'boolean' },
    output: { type: 'string', description: 'tail of each command, failures in full' },
    missing: { type: 'array', items: { type: 'string' }, description: 'what the review did not cover' },
  },
  required: ['green', 'output', 'missing'],
}

const LENSES = [
  {
    key: 'compliance',
    prompt: `Lens: STRAVA API AND AGREEMENT COMPLIANCE. Does the integration do what Strava expects?
Check scopes requested vs used vs granted (each feature checks its own scope), token refresh/rotation and storage,
revocation on disconnect and on a refused refresh, deletion of every piece of Strava data (uploads, recordings,
stored originals, derived transitions, strava_activity rows, tokens), the consent gate before Strava data is public
(including every public read path: race detail, track, laps, timeline, compare, trends, feed, page shells/OG tags —
does anything show Strava data publicly without consent?), "View on Strava" attribution and its exact text,
brand rules on the buttons, rate-limit handling (stop before the limit, never loop on 429), the webhook contract.`,
  },
  {
    key: 'security',
    prompt: `Lens: SECURITY. OAuth state generation/storage/comparison, the next= redirect (open redirect via
encoded or backslash paths?), session fixation, login when Strava is linked vs password (can the password still log
in?), the claim of a fresh instance (STRAVA_OWNER_ATHLETE_ID), a stranger's token being revoked and never stored,
tokens or secrets in any response, log line or error message, the unsigned webhook (can a forged event delete or
change anything? can it be used to make the server call Strava in a loop?), CSRF on owner writes, SQL/JSON
injection via activity names, XSS via activity names/descriptions rendered in the frontend (every interpolation
through esc()?).`,
  },
  {
    key: 'correctness',
    prompt: `Lens: DATA CORRECTNESS. The mapping (Trap 1 totals, Trap 2 measured-only power, Trap 11 local date,
unknown is null not 0, laps, partial laps, track shape identical to the FIT parser's, progress_series), triathlon
chains (ordering, gaps, untagged swims, mixed timezones, activities with 0 elapsed time, manual activities without
streams), placing onto an existing race (overlap test, race type mismatch, primaries kept), re-pull idempotence,
deleted races not returning, the tagline (race numbering, merge keeps athlete text, never duplicates, idempotent,
handles \\r\\n descriptions), purge leaving consistent primaries and transitions, reimport from stored originals,
migration 0005 matching the models (types, defaults, constraints, downgrade), concurrency of two pulls.`,
  },
  {
    key: 'frontend-efficiency',
    prompt: `Lens: FRONTEND + EFFICIENCY. Frontend: // @ts-check on every module, tokens not hex, .tnum on numbers,
every value through esc(), error/empty/loading states for each Strava call (404 when Strava is off, 401, 409, 429,
502), the consent dialog on publish, the login page in both modes, accessibility (buttons, labels, aria-live).
Efficiency: Strava requests per pull (list pages, 3 per leg, 2 per tagline — any avoidable ones? e.g. a GET before
every tagline PUT when the description is already known), N+1 database queries in the tagline loop and in
_target_race, work done inside the request that could block for long, unbounded lists in responses.`,
  },
]

function reviewPrompt(lens, focus, seen) {
  return `You are reviewing the Strava integration of this race-history app. ${lens.prompt}
${focus ? `Extra focus from the user: ${focus}\n` : ''}
${SCOPE}
${STRAVA_FACTS}

Read the scope files yourself. Report only real defects or clear violations of the spec/Strava rules, each with a
concrete failure scenario and the smallest fix. No style nits, no speculative "consider" items. Anchor each to
file:line. Do not edit any file.
${seen.length ? `Already reported in earlier rounds (do not repeat): ${seen.join(' | ')}` : ''}`
}

function verifyPrompt(lens, findings) {
  const list = findings.map((f, i) => `[${i}] ${f.title} — ${f.file}:${f.line}\n    failure: ${f.failure}\n    fix: ${f.fix}`).join('\n')
  return `You are a skeptic. For each finding below (from the ${lens.key} review of the Strava integration), try to
REFUTE it: read the code path end to end, and where possible prove or disprove it by running something — e.g. a
throwaway pytest in /tmp or the scratchpad using the FakeStrava in backend/tests/test_strava.py (Postgres must be
running: service postgresql start; tests run with cd backend && uv run pytest). Default to real=false when the
failure scenario can't actually happen, is already handled elsewhere, or contradicts docs/features/strava.md's
deliberate decisions. Do not edit repository files.
${STRAVA_FACTS}

Findings:
${list}`
}

function fixPrompt(confirmed) {
  const list = confirmed.map((f, i) => `${i + 1}. [${f.lens}/${f.severity}] ${f.title} — ${f.file}:${f.line}\n   failure: ${f.failure}\n   fix: ${f.fix}\n   verified by: ${f.evidence}`).join('\n')
  return `Fix these verified defects in the Strava integration, smallest correct change each, matching the
repository's conventions (CLAUDE.md). For each backend defect add or extend a test in backend/tests/test_strava.py
that fails before your fix and passes after. If a fix would change a decision written in docs/features/strava.md,
skip it and say why instead. Update docs/features/strava.md where behaviour changes. Run cd backend &&
uv run ruff check . && uv run ruff format . && uv run pytest -q before finishing (service postgresql start if
needed). Do not commit.

${list}`
}

// ---------------------------------------------------------------------------------------
const rounds = (args && args.rounds) || 1
const doFix = !(args && args.fix === false)
const focus = (args && args.focus) || ''
const seen = []
const confirmedAll = []
const fixes = []

for (let round = 1; round <= rounds; round++) {
  log(`Round ${round}/${rounds}`)
  const perLens = await pipeline(
    LENSES,
    (lens) => agent(reviewPrompt(lens, focus, seen), { label: `review:${lens.key}`, phase: 'Review', schema: FINDINGS }),
    (review, lens) => {
      const findings = (review && review.findings) || []
      if (!findings.length) return { lens, confirmed: [] }
      return agent(verifyPrompt(lens, findings), { label: `verify:${lens.key}`, phase: 'Verify', schema: VERDICTS })
        .then((v) => {
          const verdicts = (v && v.verdicts) || []
          const confirmed = findings
            .map((f, i) => ({ ...f, lens: lens.key, evidence: (verdicts.find((x) => x.index === i) || {}).evidence || '', real: (verdicts.find((x) => x.index === i) || {}).real }))
            .filter((f) => f.real)
          findings.forEach((f) => seen.push(`${f.file}:${f.line} ${f.title}`))
          return { lens, confirmed, total: findings.length }
        })
    },
  )
  const confirmed = perLens.filter(Boolean).flatMap((r) => r.confirmed)
  perLens.filter(Boolean).forEach((r) => log(`${r.lens.key}: ${r.confirmed.length}/${r.total || 0} confirmed`))
  confirmedAll.push(...confirmed)
  if (!confirmed.length) {
    log('Nothing confirmed this round; stopping.')
    break
  }
  if (doFix) {
    // One fixer for everything: findings share files, and parallel edits would conflict.
    const order = { high: 0, medium: 1, low: 2 }
    confirmed.sort((a, b) => order[a.severity] - order[b.severity])
    const fixed = await agent(fixPrompt(confirmed), { label: `fix:round-${round}`, phase: 'Fix', schema: FIXED })
    fixes.push(fixed)
  }
}

phase('Gate')
const gate = await agent(
  `Run, from /home/user/race-app (service postgresql start first if Postgres is down):
  cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
  for f in frontend/static/js/*.js; do node --check "$f" || exit 1; done
Report green only if all pass. Then act as a completeness critic for the Strava review that just ran: list what it
did not cover (a code path, a Strava rule, a public read path) that a next round should look at. Do not edit files.
${SCOPE}`,
  { label: 'gate', phase: 'Gate', schema: GATE },
)

return { confirmed: confirmedAll, fixes, gate }
