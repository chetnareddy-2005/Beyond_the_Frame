# Phase 10 — Headquarters and Sector Registry implementation plan

**Date:** 2026-10-24  
**Status (updated 2026-10-25):** Core registry, account, map, and authorization simulation code is present, but Phase 10 is **not signed off**. HQ controls include approved-sector counts, sector deletion with reference protection, locality coverage, an HQ map pin, and polygon-or-radius-circle allocation. Telemetry authorization now checks saved active polygon geometry (including the radius-circle polygon); focused isolated regression tests pass. An isolated real-Fernet intake/review/approval/purge flow now passes with generated test-only keys; the actual running backend environment and browser acceptance remain unverified.

## Locked requirements

- A Headquarters (HQ) administrator defines the sector count, names, identifiers, and geographic boundaries. Sector layout is not fixed to four sectors.
- Login entry offers **Sector Registry** and **Login**.
- Every member ID is strictly validated as exactly 7 digits. Registration also requires a sector address and the sector head’s phone number; HQ user cards display these details and provide audited document previews while retained.
- A registry request is for one sector and exactly three proposed members: one sector head and two technical members. Capture each member's name, member ID, rank, and role. Collect the sector head email and work ID directly, with no registration OTP; collect a work ID card for the head and both technical members for manual HQ review.
- Collect the sector head email for the shared login and invitation; the head phone number is contact metadata only. After HQ approval, send a one-time temporary password by configured SMTP. There is no registration OTP or SMS.
- HQ can review, approve, or reject a request; reviewer and decision timestamps are recorded. Every work-ID view is HMAC-chain audited.
- Each approved sector gets exactly one shared login account used by the head and two technical members.
- Sector login uses the registered email and password; the account remains server-bound to its assigned sector. Require changing the temporary password on first login.
- HQ map shows configured sectors and organization-level sector/user/registry summaries. Sector users see only their assigned sector view.
- Sector/radar schematic and geographic map use the same coordinate system. Saved active polygon/circle geometry is now authoritative for telemetry point-in-area classification; focused regression coverage verifies inside/outside points. Full API and browser acceptance remains pending.
- Pending work-ID payloads remain encrypted and available only to the assigned HQ administrator, with audited retrieval, until a decision or 30 days from submission. Approval and rejection both purge encrypted payloads and wrapped keys immediately; pending requests are purged at the 30-day hard expiry. Password reset requires a sector session, registered head ID, then explicit HQ approval; the new one-time password is emailed to the registered head address.
- Unknown, expired, or out-of-area aircraft remain review cases; authorization is not a hostile/friendly intent classifier.

## Architecture and implementation order

### 0. Existing map foundation — started

The `/authorization` simulation page now has two linked views. The sector/radar schematic and Leaflet geographic map render the same configured bounds, demo registry points, selected scenario point, and trail. Leaflet uses OpenStreetMap tiles and attribution; this is synthetic telemetry, not live GPS. Manual visual acceptance remains pending.

### 1. Identity, roles, and safe database migration

- Extend the existing SQLite account model with account role (`headquarters_admin` or `sector`), optional sector number, and first-login password-change state.
- Migrate the existing account in place as HQ administrator; preserve existing password hashing and sessions.
- Permit exactly one shared sector account for each approved sector, while keeping existing HQ administrator access.
- Add sector-number verification on sector login and enforce role authorization in API handlers, not only in the UI.
- Add a password-change endpoint for temporary credentials; use salted PBKDF2 hashes already present in the authentication layer.

### 2. HQ sector configuration and geometry — implemented

- The HQ-only `/headquarters-sectors` page uses Leaflet/OpenStreetMap to show the monitored boundary. An administrator can draw each rectangular sector by clicking two opposite corners, enter its sector ID, display name, and covered villages/localities, then save. The Available sectors cards show that coverage and provide a Delete sector action. The dashboard counts approved sector rosters rather than merely configured areas. An HQ location can be pinned directly on the map and is persisted with the central configuration. Default grid configuration requires at least two areas; custom layouts allow one to 64 areas, and the UI prevents deleting the final remaining area. IDs use `S1`, `S2`, and so on. Names are HQ-defined.
- Geometry is validated server-side: every area must be within the monitored boundary, have positive dimensions, use a unique ID, and not overlap. The layout is stored centrally in `results/authorization/headquarters.sqlite3` and returned as the authoritative sector list.
- Registry users type the requested sector name or ID. Suggestions come from the HQ list, while the server resolves the typed text to a canonical sector ID and rejects values not configured by HQ. Public intake cannot create sectors.
- A sector layout change is blocked once registry history or authorized-drone rows reference the configured sectors, preventing silent reassignment.

### 2A. Authorized-drone registry and GPS classification — backend slice implemented

- Added `headquarters_sectors` as normalized, centrally saved geometry and `authorized_drones` in the same `results/authorization/headquarters.sqlite3` database. Each device record includes ID type (flight-controller serial or GPS tracker UUID), canonical device ID, assigned `sector_id`, validity start/end, approval status, approving HQ user, and timestamps.
- SQLite foreign keys bind each drone to a configured sector. A grid/bounds update is rejected while drone records exist if it would change sector identity/geometry; this avoids silently moving a registered device's sector meaning.
- Added HQ-only `GET /api/headquarters/drones`, `POST /api/headquarters/drones` (explicit HQ-approved record creation), and `POST /api/headquarters/drones/{registry_id}/revoke` endpoints. `POST /api/authorization/telemetry` evaluates a supplied device ID, GPS coordinate, and ISO 8601 timestamp against the HQ geometry.
- Classification results are `authorized`, `other-sector`, `unknown`, and `expired/out-of-area`, with a reason code and matched/home/current sector IDs. Unknown or revoked devices remain `unknown`; no state is a hostile-intent judgment.
- This backend slice is simulation-only and not connected to hardware identity, signed telemetry, an approval queue, detector tracks, or a device credential. HQ-only APIs are a prototype authorization boundary, not hardened production device authentication.

#### Backend verification record — 2026-10-24

- `python -m py_compile` passed for the new registry/sector modules, API router, frontend proxy, and focused tests.
- Focused acceptance run passed: `backend/tests/test_authorized_drones.py` plus existing `backend/tests/test_phase10.py` — **14 passed**. Covers all four requested states, revocation, identifier normalization/validation, and rejection of sector geometry changes after registry references exist.
- A live HTTP-server test could not run in this sandbox: local TCP socket access is denied. An additional attempt to run the legacy auth API suite also hit the sandbox's system-temp permission restriction. The focused business-logic and DB checks ran successfully with isolated temporary databases; live browser/API acceptance remains outstanding.

### 3. Registry request intake

- Add a public **Sector Registry** route separate from Login.
- Request form accepts a manually typed HQ-configured sector name or ID (with suggestions), and captures the three member records, head email, and head phone as contact metadata; no OTP or SMS is used. The server resolves the text to the canonical HQ sector ID; it does not create sectors from public intake.
- Validate required fields, file type/size, and request size on both client and server. Show the request state and a non-sensitive tracking reference after submission.
- Collect one work ID card for the sector head and each of the two technical members; Aadhaar is excluded. The assigned HQ administrator manually inspects each submitted card. Pending documents remain encrypted for assigned-HQ review; approved/rejected documents are purged immediately, and pending documents are purged at 30 days. The upload is encrypted under a per-document Fernet key wrapped by BTF_REGISTRY_ENCRYPTION_KEY, stored outside the project, and crypto-shredded before file deletion. Uploads also require byte signatures matching their JPEG, PNG, or PDF media type. Access requires the assigned HQ session and BTF_REGISTRY_AUDIT_KEY.

### 4. HQ review and approval

- Add an authenticated HQ-only review queue and request detail view with protected document access, approve/reject actions, reviewer, timestamp, and reason.
- Approval must be transactional and idempotent: a sector cannot receive duplicate shared accounts and a rejected/pending request cannot authenticate.
- Keep an audit trail for request changes, document access, decisions, and account issuance.

#### HQ area-allocation controls — implemented in UI; acceptance pending

- HQ request cards now offer two allocation choices: draw a polygon by map clicks, or enter a radius from 0.1–100 km (default 5 km) and click the map to set its center. The circle is generated as a 72-point geodesic polygon and submitted through the existing geometry approval path.
- The approved sector name remains visible on the request/roster cards. Locality coverage is manually entered by HQ and shown on sector cards, registry suggestions, and review cards; village names are not inferred or geocoded.
- HQ can pin a location on the setup map. This is a manually chosen map coordinate, not a verified HQ GPS fix.
- Delete is persisted immediately. The server refuses deletion or identity/boundary changes when registry history or authorized-drone records refer to that sector, and the UI requires at least one sector to remain.
- Saved active approved polygon geometry is authoritative for telemetry point-in-area classification, including circle-created polygons. Focused tests cover inside/outside points for both legacy and trajectory-window telemetry; malformed geometry fails closed.
- No browser acceptance has yet been recorded for these latest controls.

### 5. Sector account provisioning and SMTP email adapter

- On approval, create one sector account using the registered head email as the login ID and a random temporary password; store only a password hash and force reset on first login.
- Use the standard-library SMTP adapter. Production email requires `BTF_SMTP_HOST`, `BTF_SMTP_PORT`, `BTF_SMTP_USERNAME`, `BTF_SMTP_PASSWORD`, and `BTF_SMTP_FROM`. If missing or delivery fails, send nothing and show the one-time password only in the authenticated HQ screen for manual handoff.
- After approval, email credentials only to the registered sector-head address. Never log passwords. HQ receives a one-time password response only if email delivery was not confirmed.
- No Twilio dependency is used.

### 6. Role-scoped dashboards and maps

- Implemented a sector-only route and sector-scoped dashboard API. Sector sessions are redirected away from HQ and general analysis routes; backend APIs remain HQ-gated except the scoped sector view, the user's own location, and head-initiated reset routes.
- HQ map now overlays opt-in operator-location points. Browser geolocation is requested only after an explicit action; stored coordinates are rounded to 3 decimal places (about 100 m), one latest point per account, and can be removed by that user. No location is requested during login.
- Sector map renders only the assigned sector and the current shared approximate device location. Both use Leaflet + OpenStreetMap and keep attribution.
- This is approximate operator-device location, not drone GPS. Browser/map visual acceptance and multi-user role-isolation acceptance remain pending.

### 7. Acceptance and release gates

- Legacy HQ login/session continues to work after migration.
- Pending/rejected sector registrations cannot log in; approval creates exactly one shared account bound to one sector.
- Sector login succeeds with the registered email and password; role and sector scope come from the server-side account. Sector sessions cannot access HQ APIs or another sector's records.
- First login forces password change. Temporary credentials are never persisted in plaintext or logged.
- Configured SMTP delivers the random one-time password only after approval; missing SMTP never claims delivery. Registration and reset use no OTP; reset requires a signed-in sector head ID and HQ approval; approving rotates the shared password, invalidates existing sessions, and sets the first-login password-change flag.
- Identity documents are private, encrypted at rest, role-gated, size/type-validated, audited, and deletable before accepting real documents.
- HQ and sector map boundaries match the saved sector geometry. Radar/map markers, trails, and point-in-sector labels share coordinates.
- Browser location is opt-in, distinguishable from drone GPS, revocable, and not collected on login by default.
- Narrow/desktop UI, keyboard access, error states, and map tile unavailability are reviewed. Phase 10 remains partial until this acceptance is recorded.

## Phase 10 implementation checklist — updated 2026-10-24

### Implemented in the current simulation code

- [x] HQ/sector account roles, protected API routes, one shared account per approved sector, email login, first-login password change, and head-initiated/HQ-approved reset flow.
- [x] Three-step registry intake for one sector head plus two technical members; 7-digit member IDs; head email and contact phone; sector address; three work-ID uploads; no registration OTP/SMS.
- [x] HQ request review, protected document viewing, audited access, approval/rejection, SMTP invitation adapter, and 30-day document purge logic.
- [x] HQ dashboard metrics distinguish approved sector rosters from approved roster-member totals.
- [x] HQ sector setup supports names/IDs, manually entered covered villages/localities, an HQ map pin, and persisted sector deletion with reference protection.
- [x] HQ approval UI offers drawn polygon allocation or a 0.1–100 km radius circle (5 km default) whose center is selected on the map; generated circle coordinates go through the existing approved-geometry persistence path.
- [x] Sector users receive role-scoped approved boundaries and the own-sector/neighbor-sector map styling; opt-in operator-device location remains separate from drone GPS.
- [x] Four-state synthetic telemetry classification exists for authorized, other-sector, unknown, and expired/out-of-area cases.

### Still required for Phase 10 sign-off

- [x] Use the saved active polygon/circle geometry as the authoritative telemetry geofence; focused tests cover polygon inclusion and exclusion for legacy and trajectory-window telemetry.
- [x] Real-Fernet upload, decryption/preview, approval, and immediate purge verified end to end in a disposable project using generated temporary test keys. [ ] Confirm the actual running backend process inherits its configured `cryptography`, `BTF_REGISTRY_ENCRYPTION_KEY`, and `BTF_REGISTRY_AUDIT_KEY`; do not record key values.
- [ ] Configure SMTP against a local test mail sink and verify invitation contents/first-login flow with synthetic identities; never send acceptance mail to a real mailbox or put app passwords in source files/checklists.
- [ ] Browser-accept the latest HQ sector metrics, HQ pin, locality display, sector deletion protection, polygon drawing, circle-radius selection/center placement, approval, and resulting sector map.
- [x] Registration → encrypted pending request → HQ document preview → approval → account creation → immediate document/key purge verified in an isolated real-Fernet flow with SMTP deliberately unconfigured. [ ] Verify the SMTP invitation and first-login password-change sequence through a local test mail sink and browser.
- [ ] Verify role isolation, deletion/reference behavior, document expiry/purge, password reset/session invalidation, map failures, keyboard use, and desktop/mobile layouts through the acceptance checklist.
- [ ] Decide whether application-level SQLite retry/backoff is needed beyond the existing busy timeout.

### Outside the current simulation sign-off

- [ ] Real drone GPS/telemetry, trusted device identity, detector-track linkage, and production-strength device authentication remain future integrations; simulated states must not be described as live operational tracking.

**Phase 10 status: partial.** Saved polygon/circle geofencing, document signature checks, and the isolated real-Fernet registry flow are verified. Browser visual/role-isolation acceptance, live SMTP delivery, process-level environment confirmation, API tests requiring local sockets, and broader regression failures remain open. Do not mark Phase 10 complete until the remaining sign-off items above are resolved.

## Real-world services and readiness

| Capability | Prototype integration | What is needed for production |
| --- | --- | --- |
| Geographic map | Leaflet + OpenStreetMap public tiles (no key) for this prototype | If usage grows beyond public tile capacity/policy, select a hosted OSM-based tile provider; keep attribution and set usage limits |
| Operator-device location | Browser Geolocation API, only after opt-in | HTTPS deployment, privacy notice, retention and access policy |
| Drone GPS | Not connected; simulation only | Trusted GPS/telemetry source, device identity, timestamp/replay safeguards |
| Email | SMTP adapter; fail closed if unconfigured | Configure SMTP host, port, username, password, and sender address in the backend environment |
| Member work IDs | Three encrypted uploads (head + two technical members) with member-specific HQ review routes; disabled until keys are configured | Review per-document key configuration, private-storage permissions, audit-key custody, and retention worker in deployment |

## Locked operational decisions and local setup

- The sector head and both technical members upload work IDs; Aadhaar is excluded. Pending documents remain available only to the assigned HQ reviewer for up to 30 days. Approval and rejection trigger immediate purge; pending requests are purged at the 30-day hard expiry. The service deletes the wrapped per-document key and encrypted file, enables SQLite secure-delete, and truncates the WAL checkpoint. Filesystem/journal/backup copies outside the service require deployment-level retention control.
- Only the assigned HQ admin can inspect a pending document. Configure BTF_REGISTRY_AUDIT_KEY before review. Generate independent secrets outside the source tree; never commit them.
- Install the declared cryptography dependency, then configure BTF_REGISTRY_ENCRYPTION_KEY with a valid Fernet key and BTF_REGISTRY_AUDIT_KEY with at least 32 random bytes. Both are required for intake, queue review, document access, and decisions. The user reported installing cryptography and generating user-scope keys locally; verify that the running backend process inherits the settings. Never copy secret values into the repository or checklist.
- Shared password reset is head-initiated: the signed-in shared account must supply the registered head member ID, HQ reviews the head-ID request directly; no OTP is sent. Reset invalidates all sessions and forces a password update.
- SMTP is the invitation adapter. Without complete configuration, no email is sent and no message text is stored; the HQ UI displays the random one-time password once for manual handoff. No registration or reset OTP is used.
- Leaflet continues to use public OpenStreetMap tiles with attribution for the prototype. If volume exceeds public capacity/policy, configure a dedicated Stadia/Mapbox tile URL and restricted client token without changing coordinate logic.
- Added 15-second SQLite busy timeouts (SQLite retries lock waits during that window). Application-level transaction replay/backoff is not implemented yet. Remaining Phase 10 work: confirm the live backend inherits its security keys, exercise SMTP through a safe test sink, verify sector/HQ role isolation and opt-in location behavior in a browser, and record desktop/mobile/API acceptance. Application-level transaction replay/backoff is not implemented; SQLite uses a 15-second busy timeout. Do not mark the whole phase complete yet.


### Latest verification update — 2026-10-25

- `py_compile` passed for the touched backend modules. `backend/tests/test_authorized_drones.py` plus `backend/tests/test_sector_registry.py`: **19 passed**. `backend/tests/test_phase10.py`: **10 passed**.
- Real Fernet flow passed using disposable generated keys and synthetic identities/documents: submit all three IDs, decrypt/preview each, approve with allocated polygon, verify SMTP-unconfigured fallback sends nothing, then verify immediate encrypted-file and wrapped-key purge. The owner’s running-service environment and real SMTP delivery remain unverified.
- `node --check frontend/app.js` passed. Broad frontend JS tests: **53 passed, 22 failed**; failures concern dashboard alert/trajectory test cases and require triage; they are outside the focused Phase 10 test set. Broader backend API/auth HTTP tests could not complete because this environment blocks loopback TCP sockets. A few existing contract checks also fail.
- Browser review prompt: `docs/PHASE10_COPILOT_BROWSER_REVIEW_PROMPT.md`. Use a disposable database and local mail sink; do not use the real registry DB or a real email inbox.

### Email-based registration update — 2026-10-24

- Registration remains email-invitation only with no OTP or SMS; the sector-head phone number is collected as contact metadata and shown only to HQ. The retired OTP challenge table is dropped when initializing the registry database.
- HQ manually reviews the head and two technical-member work IDs. Approval creates one shared account whose username is the registered head email. With SMTP configured, a random one-time password is emailed; the account forces password change, logs out, and returns to sign-in so the head signs in again using the registered email and new password.
- A fixed `test@123` password is deliberately not used: it would be a shared guessable credential. Local SMTP fallback sends no mail and reveals the random one-time credential only once to HQ for secure manual handoff.
- SMTP settings: `BTF_SMTP_HOST`, `BTF_SMTP_PORT` (default 587), `BTF_SMTP_USERNAME`, `BTF_SMTP_PASSWORD`, `BTF_SMTP_FROM`.
- Verification after this update: 26 focused backend tests passed; `node --check frontend/app.js` and Python syntax compilation passed. Live SMTP and browser acceptance still require local configuration.

