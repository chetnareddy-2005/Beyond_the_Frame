# Phase 10 browser acceptance prompt for VS Code Copilot

Use this prompt in VS Code Copilot with the Behind the Frame project open. Run it only against a disposable local test environment.

```text
Act as a careful senior QA engineer reviewing Phase 10 of the existing Beyond the Frame application. Inspect the existing implementation first and use the real local routes, API contracts, and role/session model found in this repository. Do not assume a framework or invent endpoints.

Goal: execute a browser-based acceptance review of HQ sector setup, registry review, sector user isolation, and approved sector maps. Fix only clear defects you can reproduce, preserve established design patterns, and report every change. Do not claim Phase 10 complete merely because the browser loads.

Safety constraints:
- Use a disposable test database and private-document directory. Never reset, delete, migrate, or seed the owner's live `results/authorization/headquarters.sqlite3`, auth database, or real private document storage.
- Use synthetic test identities, exactly 7-digit member IDs, dummy document files, and test-only SMTP/inbox capture (such as a local mail sink). Never send an invitation to a real mailbox, expose real credentials, or print secrets.
- Do not use actual drone telemetry or real operator location. Keep browser geolocation denied unless specifically checking the opt-in flow; if checking it, use the browser's mock location.
- Do not weaken authentication, authorization, validation, retention, or cryptographic controls to make a test pass. Do not add hard-coded credentials or secrets.
- Do not change unrelated phases, model weights, labels, detector thresholds, datasets, or production data. Do not perform destructive decommission/delete operations outside disposable test data.

Review and exercise these flows using the application's actual UI:
1. Login and role routing: HQ admin can reach HQ pages. A sector account is sent to its scoped dashboard, cannot navigate into HQ/analysis pages, and receives server-side denial for HQ APIs even if it manually changes the URL or request parameters.
2. HQ dashboard: displayed approved-sector and user totals are internally consistent; each management card/link opens its matching page; pending review counts reflect test state.
3. HQ Sector Setup: draw/save a named polygon sector; create a radius circle using a synthetic center and a chosen radius (check the displayed radius/area label); verify sector name, locality coverage, HQ pin, and saved geometry after refresh. Verify Delete sector is available only where safe, is blocked when referenced, and never allows removing the final required sector. Confirm no stale “Continue drawing / Drawing paused” helper remains if the current design removed it.
4. Registry request: submit a synthetic request for a configured sector with head email, contact phone, address, three distinct names/ranks and valid 7-digit IDs, and three fake supported documents. Check invalid ID lengths, MIME/signature mismatch, oversized files, duplicate IDs, missing fields, and unconfigured sectors are rejected with understandable messages. Never upload real identity documents.
5. HQ review: the assigned HQ reviewer can open each retained ID in an inline preview/modal (no forced download), see the submitted head email, phone, address, and allocated sector name, and approve or reject. Verify unauthorized HQ users and sector users cannot fetch documents. On approval, confirm the approved area is bound to that sector, a test-only invitation is captured by the local mail sink, and temporary credentials are never exposed in logs. If SMTP is deliberately unconfigured, confirm the UI presents a one-time fallback only to the authenticated HQ reviewer and does not persist the plaintext password or message.
6. First login/reset: use only the synthetic invitation; first login forces password change and returns the user to login. Confirm the new password works. If checking head-initiated reset, use test identities, confirm HQ approval is required, then verify previous sessions stop working.
7. Sector map: the account sees its own active boundary in pink and other active boundaries in yellow, with correct names/coverage labels. The map fits the assigned geometry and refreshes from saved server data. Decommissioned sectors disappear from active map/API responses while historical records remain; test only on the disposable database.
8. Geofence behavior: verify telemetry point-in-polygon uses saved polygon/radius geometry, including an inside point, outside-but-within-the-old-bounding-box point, boundary point, and a point in a polygon hole if holes are supported. Check legacy single-point and trajectory-window payloads. Do not label a bird/drone heuristic as real sensor intelligence.
9. UI quality: inspect HQ and sector views at desktop (1440x900) and narrow mobile (390x844). Check overflow, sticky headers/sidebar, cards, form labels, error/success states, map control reachability, keyboard focus, accessible names, and console/network errors. Capture screenshots for key pages and both responsive sizes.

Execution and reporting:
- First state which local test services, test accounts, and test mail sink are available. If any are unavailable, continue with all independent checks and list the blocked ones; do not ask for or inspect secrets.
- Use screenshots plus browser console/network inspection as evidence. Keep synthetic state isolated and clean it up only within the disposable test workspace.
- For every check, record PASS / FAIL / BLOCKED, exact route, steps, expected and actual result, and a screenshot or sanitized console/network evidence. Redact cookies, tokens, password values, document bytes, and personal data.
- If you fix a reproduced issue, show the file(s) changed and rerun the affected browser/API acceptance case. Do not conceal failures or call unrun checks passed.
- Finish with a Phase 10 gate table separating: automated tests, browser checks, environment-dependent checks (Fernet keys / SMTP), unresolved defects, and production integrations that are explicitly out of scope. Recommend COMPLETE only if all required gates pass; otherwise mark PARTIAL and give the next exact action.
```
