# Phase 10 — Authorized drone simulation MVP

**Status:** 🟡 Simulation UI/manual acceptance remains pending; HQ-owned sector and authorized-drone backend slice implemented with focused tests passing. Later implementation updates in this file supersede the earlier proposed-work status below.  
**Scope:** Demonstrate sector registry checks using synthetic IDs and coordinates. This phase work does not connect to actual GPS hardware or a live authorization provider. The dashboard now uses Leaflet with OpenStreetMap tiles to display synthetic sector bounds and simulated locations.

## Implementation plan

1. Keep visual detection and authorization separate. `Bird/Drone` model output must never establish friend/hostile identity.
2. Provide a small, synthetic registry with home sector and permission validity window.
3. HQ configures a monitored boundary and draws 2–64 named rectangular sectors; the authorized-drone simulation evaluates device ID, GPS coordinates, and timestamp against that shared geometry. The older operator-owned rows × columns grid remains for the separate simulation page.
4. Return explicit states: `authorized_home_sector`, `registered_other_sector`, `unknown_id`, `gps_unavailable`, `permission_expired`, or `outside_monitored_area`. Review-required cases are never called hostile.
5. The authenticated HQ dashboard and sector dashboard use Leaflet/OpenStreetMap with server-owned geometry. The separate authorization simulation page retains synchronized synthetic sector/radar boundaries, trail, and scenario position.
6. Preserve simulation labels throughout the interface. Real GPS and hardware identity remain future work. HQ-managed sector geometry and the authorization registry persist in the HQ SQLite database; live security/provider and browser acceptance gates remain.

## Added behavior

- `backend/authorization.py` holds validation, synthetic registry cases, configurable sector generation, permission windows, and the deterministic evaluator.
- `GET /api/authorization/simulation` supplies the signed-in user's saved sector config, generated grid, synthetic registry, and scenarios.
- `POST /api/authorization/config` validates and persists grid settings per user in `results/authorization/settings.sqlite3`; `POST /api/authorization/evaluate` checks telemetry against that grid and returns a conservative state. The dashboard proxy allows only these bounded JSON POSTs.
- `/authorization` adds an **Authorized Drones** view. It renders the configurable sector/radar grid alongside a Leaflet/OpenStreetMap geographic view; both use the same configured bounds, synthetic registry positions, selected scenario marker, and trail. It also shows registry rows and five scenarios: home-sector match, sector crossing, unknown ID, unavailable GPS, and expired permission. OSM attribution is shown by Leaflet; tile use must follow the OSM tile usage policy.
- `backend/auth.py` now migrates the existing users table with roles, sector number, and first-login password state; includes per-sector shared-account provisioning and sector-number credential validation. The existing account remains the Headquarters administrator.
- Backend API routes now have a Headquarters-only gate by default; login/session returns role details, and a password-change API foundation is present. At this initial implementation stage, sector sign-in still asked for a sector number and first-login reset UI was unfinished; the later login-flow update below supersedes that status.
- All page copy identifies coordinates and records as simulated and states that authorization is not a hostility judgment.

## Manual acceptance checklist (not yet executed)

- [ ] Signed-in user can open **Authorized Drones**; signed-out API requests remain protected.
- [ ] Home-sector scenario returns `authorized_home_sector`.
- [ ] Configured non-home-sector unit appearing in the home sector returns `registered_other_sector`.
- [ ] Unknown ID returns `unknown_id` and does not say hostile.
- [ ] Missing GPS returns `gps_unavailable`; expired record returns `permission_expired`.
- [ ] Out-of-range coordinates are rejected; a valid coordinate beyond the configured area returns `outside_monitored_area`.
- [ ] Dashboard displays the selected scenario's trail/current point and simulation disclaimer on desktop and narrow layout; confirm the sector/radar view and Leaflet map show the same coordinates and sector assignment.
- [ ] Save changed rows/columns/bounds, navigate away/back or restart the backend, and confirm the config persists.
- [ ] Configure another account and confirm its saved grid does not overwrite the first account's grid.

## Limitations and next work

This remains a deterministic demonstration, not production authorization: synthetic scenario records and coordinates do not represent live GPS. No real GPS signatures, replay protection, device authentication, or detector-track association is integrated. HQ-managed sectors and a persisted authorized-drone registry are described in later implementation updates. OSM public tile service is used for the prototype and is not a guaranteed unlimited production tile provider. None of these scenario outcomes overrides visual model evidence or establishes threat intent.

## Proposed Headquarters and Sector Registry workflow (requirements captured 2026-10-24)

**Historical status at the time this proposal was written:** HQ sector grid, relational authorized-drone registry, telemetry classifier, and role-aware authentication foundation were implemented; the registration workflow was still proposed. Subsequent implementation updates later in this document describe what has since been added. This workflow remains a simulation/prototype and must not be represented as live identity verification, live GPS, or operational authorization until services and security controls are configured and accepted.

### Roles and entry points

- Login page offers two distinct actions: **Sector Registry** and **Login**.
- A Headquarters administrator manages the organization, sector definitions, registry requests, account provisioning, and organization-wide map view.
- Headquarters defines sector count, names, and geographic boundaries. The layout is not hardcoded to four sectors; a new registry request selects one of the currently configured sectors.
- Approved sector users belong to one assigned sector. Their dashboard and registry actions are limited to that sector; Headquarters has organization-wide visibility.

### Sector registration and approval

- A sector registration request contains exactly three proposed members: one sector head and two technical members.
- For each of the three members, collect name, member ID, rank, role, and that member’s work ID card for HQ review. Aadhaar is not requested. Collect an email address only for the sector head; no phone number or registration OTP is used. The request also contains the requested sector.
- Keep the request in **Pending Headquarters Review** until an administrator approves or rejects it; record reviewer, decision, and timestamp. Use the head email only after approval to deliver the invitation; do not use it for unrelated messaging.
- On approval, create exactly one shared login account for that sector. Use the registered head email as the shared account login and send a random one-time password by configured SMTP only after HQ approval; require a password change at first login and never send credentials before approval. Sector sign-in offers only active HQ-approved sectors and requires the selected sector to match the shared account. HQ Admin sign-in remains username/password based.
- **Account model decided:** one shared account per sector, used by the sector head and its two technical members. Do not create separate member accounts or collect technical-member phone numbers. Record this as a prototype requirement; operational auditability should identify the acting member within the shared session if the project later needs per-person accountability.

### Headquarters map and dashboards

- Headquarters dashboard shows all configured sectors and boundaries on a map, sector totals, pending/approved registry counts, and user/member details subject to role permissions.
- “Where the application is being used” must be based on an explicit, visible location-sharing choice and the browser/device permission. Show location freshness and whether a point is approximate; provide a disable/revoke path. Never imply that a static login record is live GPS.
- Sector dashboard shows only its assigned sector, its approved members, and sector-authorized drone registry/telemetry. The paired sectoral/radar view and geographic map must use the same coordinates so the marker and sector assignment stay synchronized.

### Security and acceptance gates

- Do not put administrator credentials in frontend code or documentation. Configure the initial administrator securely, require password change/MFA as appropriate, and rotate any credential disclosed in chat.
- Earlier proposal: only the sector head work ID was planned. The updated three-step intake below supersedes this and collects work IDs for the head and both technical members; Aadhaar remains excluded. The assigned HQ administrator manually reviews it. Updated retention policy: rejected payloads are purged immediately; approved payloads remain encrypted and available only to the assigned HQ administrator with audited retrieval for up to 30 days from submission, then are purged. Intake/review stay disabled until the encryption and audit keys are configured.
- Use authenticated role-based APIs, protected document storage, validation and audit logs for approvals, and secure credential provisioning and first-login password reset; Email delivery is limited to the sector-head address and happens only after approval. SMTP must be configured; otherwise show the random one-time credential only to HQ and never claim an email was sent.
- Acceptance requires tests for pending/approve/reject flows, exactly one shared account per approved sector, sector isolation, approved-sector login selection and matching, first-login password reset with an 8–12 character uppercase/lowercase/number/symbol password, map/sector coordinate consistency, location opt-in/revocation, and document access restrictions. OSM tile network availability/policy and real GPS feed are separate integration gates.

## Authorized-drone registry backend slice — 2026-10-24

- SQLite database: `results/authorization/headquarters.sqlite3` now holds `headquarters_sector_config`, `headquarters_sectors`, and `authorized_drones`. Device ID is typed as a flight-controller serial or GPS tracker UUID; approved rows link through a foreign key to a sector and include validity dates and HQ approver metadata.
- `GET /api/headquarters/drones` lists records for HQ; `POST /api/headquarters/drones` creates an HQ-approved record after manual review; `POST /api/headquarters/drones/{registry_id}/revoke` revokes it. `POST /api/authorization/telemetry` returns `authorized`, `other-sector`, `unknown`, or `expired/out-of-area` plus a reason and sector IDs. These routes remain HQ-authenticated and simulation-only.
- Sector geometry changes that would alter generated sector rows are blocked while drone records reference the sector table. A controlled reassignment/migration workflow is still needed.
- Device-origin authentication, signed/replay-protected telemetry, persistent telemetry retention, work-ID upload encryption/retention, frontend registry management, and detector-track linking are not implemented. Do not treat this as an operational authorization or threat-classification service.
- Focused checks passed on 2026-10-24: 4 new authorized-drone backend acceptance tests plus 10 existing Phase 10 tests (14 total), and Python syntax compilation. Live HTTP/browser acceptance remains pending because this sandbox blocks local TCP socket access.
- Earlier document decision: head-only work ID. Superseded by the three-member work-ID intake update below; Aadhaar remains excluded. Only the assigned HQ administrator can inspect the document. Rejected documents are purged immediately; approved documents remain available only to the assigned HQ reviewer for up to 30 days, then are purged. Secure-key configuration is still required before the form accepts uploads.
- Integration choices: SMTP is the invitation adapter. Until configured, send no email and persist no message text; show a random temporary password once in the authenticated HQ UI. No OTP is used for registration or password reset. Keep Leaflet + OSM public tiles for the prototype; choose a hosted OSM-based tile provider if usage outgrows public capacity/policy.


## Headquarters registry implementation update — 2026-10-24

- Updated public /sector-registry intake to three steps: sector/head details with email and head work-ID upload (no phone/OTP); two technical members with work-ID uploads; and request confirmation/login link. All three documents are encrypted and separately reviewable by the assigned HQ reviewer.
- Added the HQ review queue on /headquarters-sectors; requests are assigned to the configured HQ admin. Work-ID retrieval checks the active HQ role/session and reviewer assignment and appends an HMAC-SHA256 chained audit record.
- Work-ID payload is Fernet-encrypted with an independent per-file key. The per-file key is wrapped using BTF_REGISTRY_ENCRYPTION_KEY; files are stored outside the project under sibling secure_private_storage/work_ids by default. Upload/review fail closed until cryptography and BTF_REGISTRY_ENCRYPTION_KEY/BTF_REGISTRY_AUDIT_KEY are available.
- An hourly worker deletes the wrapped key and encrypted file, enables SQLite secure-delete and truncates the WAL checkpoint for final decisions or after 30 days. Decision endpoints also purge immediately. The database retains non-sensitive purge metadata only. External backup/snapshot retention is outside this local worker.
- With SMTP unconfigured, credential delivery sends no email and writes no message outbox. The decision response carries the temporary password once to the authenticated HQ UI, which clears it after 90 seconds. Password reset requires head ID and explicit HQ approval; no OTP is requested.
- Shared-account reset requires a signed-in sector session, registered head ID, and HQ approval. Approval rotates the shared password, invalidates sessions, and marks the next login for password change.
- Focused verification (latest): 11 registry and operator-location tests passed; Python compilation and node --check frontend/app.js passed. Live HTTP/browser acceptance remains outstanding. Whole Phase 10 remains partial until role isolation, location-sharing UI, map behavior, and HQ handoff receive end-to-end browser acceptance.


## Current Phase 10 implementation status — 2026-10-24

- HQ can draw rectangles, assign unique sector IDs and manual display names, remove areas, and persist the layout. Registration allows typed sector names/IDs, shows suggestions from HQ, and server-side resolution accepts only an exact configured name/ID.
- Removed the registration phone/OTP routes from the frontend proxy and raised the request-size cap to fit three maximum-size base64-encoded ID documents.
- Corrected wrapped document-key decryption order. Focused authorized-drone + registry tests: 16 passed; Python compilation and frontend JavaScript syntax checks passed.
- Actual encrypted uploads and email delivery cannot be confirmed in this local environment: `cryptography`, encryption/audit environment secrets, and SMTP credentials are absent. Live browser/API review remains; simulation work must not be represented as real GPS authorization.

- Registration and account approval now use the sector-head email; a random one-time password is sent by SMTP after HQ approval. Sector login uses email/password plus a selection from active HQ-approved sectors; the backend verifies the selected sector matches the shared account. First password change requires 8–12 characters including uppercase, lowercase, number, and symbol, then logs out and returns to the login page.
- Login-flow checks: `pytest backend\tests\test_auth.py -q` passed (7 tests, 15 subtests); the approved-sector filtering helper, track API scope, focused frontend tests (16), JavaScript syntax, and Python compilation passed. A fresh isolated server returned HTTP 200 with an empty approved-sector list, without authentication. The already-running browser server at `127.0.0.1:8001` returned HTTP 401 for that public route, so the sector selector could not load there; no credentials were entered. Restart/reload the local backend and repeat the browser check before accepting the live login flow.
- Sector application navigation now includes Dashboard, My Sector, Live CCTV, Video Analysis, Results, History, Alerts, Reports, and Settings. Sector run/history APIs are owner-scoped; Headquarters management, global analysis data, and authorization simulation remain unavailable to sector accounts. Focused navigation checks and isolated sector-ownership/API-denial tests passed. A signed-in browser acceptance run still requires a disposable approved sector account.
