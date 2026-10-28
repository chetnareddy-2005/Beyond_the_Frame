# Phase 2 — Requirements freeze

**Status: COMPLETE**  
**Specification version:** 1.0  
**Freeze date:** 2026-10-22

## Frozen acceptance contract

The canonical requirements and scenario matrix are preserved in PHASE2_TEST_SCENARIOS_v1.0_LOCKED.md. Its SHA-256 is $hash ($bytes bytes). The machine-readable freeze record is reeze_manifest.json.

The locked requirements cover:

- V1 mixed birds and drone: separate visual classes and object identities; bird-only tracks must not trigger UAV alerts; drone alerts remain explicitly unverified review cues.
- V2 multiple drones: simultaneous drones keep separate IDs, alerts, and evidence; merges and identity errors are measured.
- V3 bird-only flock: zero UAV alerts is a safety criterion; missed/uncertain birds remain visible in results.
- Shared detection/tracking metrics, whole-sequence split rules, small/far targets, occlusion, thermal limits, alert evidence/timing, dashboard count reconciliation, and the boundary between visual class and authorization.

## Data status and scope boundary

This phase locks what the project must satisfy; it does not claim that the required videos or labels are already available. Current local assets do not provide eligible, reviewed V1 mixed-class or V2 multiple-drone test videos. SMOT4SB pilot sequences are training-split development data only. The two local flock videos still need rights confirmation and reviewed annotations before they can serve as held-out V3 data.

Those gaps stay assigned to later data-readiness/evaluation work. No requirement was relaxed to fit the available data. Until suitable data is prepared, V1/V2 and independent V3 acceptance are reported as **not evaluated**.

## Change control

Do not adjust acceptance criteria after seeing test results. If requirements genuinely change, publish a new specification version with date and reason before running the affected evaluation. Preserve v1.0 and label results with the version used.

## Phase 2 exit

**Requirements freeze is complete.** Dataset acquisition, rights review, frame/label audit, split creation, and model evaluation are separate follow-on tasks. This status is not a model-accuracy or scientific-validation claim.
