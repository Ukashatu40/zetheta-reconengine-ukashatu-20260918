# Reconciliation desk (web dashboard)

The operations dashboard for the reconciliation engine: React 19, TypeScript (strict), Vite,
TanStack Query. No UI framework and no chart library; the interface is built from the design tokens
in `src/styles/` and a few hundred lines of SVG.

## Run it

```bash
# with the whole stack (nginx, port 8080)
docker compose up -d --build
docker compose exec api python -m recon.cli.seed --scale 3 --weeks 6    # demo data, prints three keys
open http://localhost:8080

# for development (hot reload, port 5173, forwards /api to the engine on :8000)
make web-install && make web-dev
```

Sign in with an API key. A VIEWER key can read everything; an ANALYST can also resolve and assign
exceptions, review matches, upload files and start runs; an ADMIN can also resolve tier 3 and 4
exceptions and write items off. The interface hides what the key cannot do, and the engine
enforces it regardless.

## Views

| Route         | What it is for                                                                                                                                                             |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/` Overview  | The match rate drawn as a tally, headline figures, 7-day trends, a per-bank health grid, exceptions by category, newest alerts, and the three compliance reports           |
| `/exceptions` | Filterable list with a live SLA countdown; each row opens the ledger entry and the bank entry side by side, the engine's reasoning, history, and resolve / assign / reopen |
| `/reviews`    | Fuzzy matches held for a human, with per-field agreement, confirm or reject                                                                                                |
| `/alerts`     | Anomaly alerts, acknowledge                                                                                                                                                |
| `/audit`      | Searchable, paginated trail; every entry expands to a before/after diff and its hashes; one button verifies every hash chain                                               |
| `/files`      | Ingested files with record and error counts, SHA-256, integrity check, upload                                                                                              |
| `/runs`       | Run history with per-stage timings; start a run                                                                                                                            |

Filters and page position live in the URL, so any view can be bookmarked or sent to a colleague.

## Design

One system, two themes: **Clearing house** (light) and **Night desk** (dark), chosen from the
sidebar or following the operating system. The recurring motif is the tally mark: the overview draws
the match rate as one hundred tally strokes (solid where matched, dashed red where not), and an
exception draws its ledger entry and bank entry as two slips joined by a line that is struck when
they match and broken when they do not.

- Tokens (colour, type scale, spacing) are CSS custom properties in `src/styles/tokens.css`;
  components never use raw colours.
- Type: Bricolage Grotesque for headings and figures, Geist for the interface, Geist Mono only for
  hashes and identifiers. Fonts are bundled (no third-party font requests). Money is grouped the
  Indian way (12,50,000) and times are shown in IST.
- Severity is a four-step meter plus its word, never colour alone. Charts have a table for screen
  readers; every control is reachable by keyboard; motion is limited to the one draw-in on the
  overview and is switched off under `prefers-reduced-motion`.
- Responsive down to a phone: the sidebar becomes a top bar and wide tables scroll.

## Security and privacy

- **The key stays in memory.** It is never written to `localStorage`, a cookie or a URL. Ticking
  "Keep the key for this tab" uses `sessionStorage`, which dies with the tab. After 15 minutes
  without input the key and every cached response are discarded and the lock screen returns. A 401
  from any call locks the desk at once (a revoked key or deactivated user stops working immediately).
- **One origin.** nginx serves the site and forwards `/api` and `/health` to the engine, so there is
  no CORS configuration and the key never crosses an origin. Requests use `credentials: 'omit'`,
  `cache: 'no-store'` and `referrerPolicy: 'no-referrer'`.
- **Strict CSP** (`default-src 'self'`, no inline script, no third-party origins), plus
  `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a locked-down
  `Permissions-Policy` and cross-origin isolation headers (`security-headers.conf`). The container
  runs as an unprivileged user.
- **No injected HTML.** Everything from the engine, reports included, is rendered as text by React;
  an ESLint rule forbids `dangerouslySetInnerHTML`, and tests feed markup-looking values through the
  audit diff and the report dialog.
- **Privacy switch.** "Hide amounts" replaces every money figure, for screen sharing. Account numbers
  are shown only in the engine's masked form, and narrations are PAN-masked by the engine.
- The page tells search engines not to index it (`noindex`).

## Keeping it maintainable

- `src/api/schema.d.ts` is generated from the engine's OpenAPI schema (`make api-types`), so a
  backend change that breaks the UI fails the type check. A backend test fails if
  `openapi.json` is stale, and CI fails if the generated types are.
- Layers: `api/` (the only code that touches the network), `auth/` (the key and the lock),
  `lib/` (pure formatting, SLA and diff logic), `components/` and `charts/` (shared pieces),
  `features/<view>/` (one folder per page, with its own CSS).
- Server state lives in TanStack Query: queries refetch on their own, mutations invalidate exactly
  the queries they can change.
- Adding a view: add an endpoint in `api/endpoints.ts`, a hook in `api/queries.ts`, a folder under
  `features/`, a route in `App.tsx`, a link in `components/Shell.tsx`.

## Checks

```bash
npm run check    # typecheck, ESLint, Prettier, 48 tests (Vitest + Testing Library)
npm run build    # production bundle (about 110 kB of JavaScript, gzipped)
```

The tests cover the API client (headers, error mapping, 401 handling), sign-in and the idle lock,
formatting and SLA logic, the audit diff, the charts, role-based actions and that report and diff
content is never parsed as HTML.
