# Phase 10: Integration of the attached implementation proposal

## Decision

The proposal's useful architecture is already represented in the Phase 10 code. Its sample files are not drop-in code for this repository: they assume FastAPI routes, bearer-token authentication, table names, and schemas that the project does not use. The project uses its existing HTTP server, cookie-backed `AuthManager` sessions, and the Headquarters registry database. The proposal is therefore integrated as a set of design checks and traceability references, rather than copied verbatim.

## Feature-to-project mapping

| Proposal concept | Existing implementation | Project behavior |
| --- | --- | --- |
| Normalize telemetry from multiple source/replay formats | `backend/sensor_adapters.py` | Bounded JSON observation windows and CSV replay parsing validate source metadata and coordinates before classification. Source labels do not imply a live hardware connection. |
| Keep motion class separate from authorization | `backend/trajectory_classifier.py`, `backend/authorized_drones.py` | The classifier produces `drone`, `bird`, or `unknown`; registry validity and active geofence membership are evaluated separately as authorization state. The classifier does not approve devices or determine intent. |
| Persist tracks, observations, and state changes | `backend/authorized_drones.py` | `trajectory_latest`, `trajectory_points`, and `trajectory_events` persist the current assessment, chronological observations, and auditable lifecycle/review changes. The existing HQ database is initialized through the project's migration helpers. |
| Enforce role and sector scope on the server | `backend/server.py`, `backend/app.py`, `backend/auth.py` | Requests derive the role and sector from the authenticated server-side session. Sector users cannot use a client-supplied sector ID to widen track reads or perform HQ review actions. |
| Decommission without deleting history | `backend/sector_registry.py`, `backend/headquarters_sectors.py`, `backend/auth.py` | HQ records a reason, marks the configured area and approved request inactive, revokes its authorized devices, records an audit event, and deactivates the shared sector account while preserving history. Active geometry queries omit inactive sectors. GPS authorization now evaluates saved HQ-approved polygon geometry (including polygon holes) rather than treating its bounding rectangle as the approved area. |
| Show own and neighboring sector geometry | `backend/headquarters_sectors.py`, `backend/server.py`, `frontend/app.js`, `frontend/styles.css` | The sector dashboard loads active geometry from the backend, styles the assigned sector pink and other active sectors yellow, and displays the server-provided name and area label. |

## Security and compatibility rules retained

- Authentication continues to use the real session cookie and `AuthManager`; do not add sample tokens such as `hq_admin_token` or `sector_token_S1`.
- Role checks remain in the real request handler. A helper that trusts a caller-provided role, sector number, or account ID is not an authorization boundary.
- Device classification, device authorization, geographic sector membership, and human HQ review are distinct fields and decisions.
- Track history is retained for review. Decommissioning filters operational views and invalidates the sector account; it does not hard-delete historical requests or audit trails.
- The map and GPS authorization use the backend's saved active sector geometry. Point-in-polygon checks include polygon boundaries and exclude holes; malformed stored geometry fails closed. The UI must not ship example sector coordinates as if they were live configuration.
- Current motion classification is a synthetic/demo heuristic over GPS-like points. The source adapter is ready to normalize inputs; it is not a radar, camera, or acoustic sensor integration. Uploaded work IDs now require JPEG, PNG, or PDF byte signatures matching their declared MIME types.

## Sample-code issues intentionally not copied

The attached proposal contains several incompatible or unsafe examples: hard-coded mock authentication, endpoints that bypass this server's role guards, unrelated table/column names (`accounts_metadata`, `target_tracks`, and `authorization_status`), a classifier that labels almost every low-speed path as a bird, and illustrative Leaflet code with incomplete icon options and mocked sector data. Copying these snippets would create parallel schemas and could undermine the existing session boundary. Their underlying ideas are covered by the project modules listed above.

## Test safety

`create_server()` no longer starts the document purge worker by default. The production launcher explicitly enables it; test-created servers do not start background cleanup against the developer project root. Test suites should continue using temporary project roots and storage paths.

## Transaction boundary and follow-up

The registry and login accounts currently live in separate SQLite databases. Decommissioning commits the Headquarters registry state and then deactivates the sector account. If account deactivation fails after the registry commit, the request guard still checks the persisted sector-active flag on subsequent requests and rejects the inactive sector session. This is fail-closed, but it is not a single cross-database atomic commit. A future persistence redesign can colocate these records or add a durable reconciliation/outbox workflow if strict all-or-nothing cross-database semantics become necessary.

## Readiness

This integration note records the proposal's applicable architecture and the implementation locations. It does not certify Phase 10 as complete. Browser visual acceptance and the remaining manual end-to-end review gates must still pass before sign-off.
