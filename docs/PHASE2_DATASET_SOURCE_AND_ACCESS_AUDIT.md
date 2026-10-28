# Phase 2 dataset source and access audit

**Review date:** 2026-10-22  
**Method:** repository records plus official provider/project pages and the official Figshare API. UB-SOD annotations and split metadata were downloaded from the public provider record; no gated request, login, DUA, license acceptance, or credentials were used.

## V1 — mixed birds and drone

**Candidate:** WOSDETC / Drone-vs-Bird Detection Challenge.

- Official project repository: <https://github.com/wanghaol23/challenge-data>. Official challenge page: <https://wosdetc2023.wordpress.com/drone-vs-bird-detection-challenge/>. The page describes sequences where a drone appears while birds are present and asks that alarms/positions be produced only for drones.
- **Access: request-gated; DUA/signature required.** The repository instructs users to email `wosdetc@googlegroups.com`; organizers then provide a data-use agreement. There is no public DUA PDF or signing portal linked on the official page, so no exact DUA document URL is available. Dataset download is not publicly available from the page.
- The official description says the dataset may be used for research after signing. Detailed obligations, redistribution rules, derivative/annotation rights, retention, and commercial-use terms are not publicly specified; they must be read in the provider-supplied DUA. No additional license terms are inferred.
- The official annotation format is per-frame rows with frame number, object count, boxes, and class. The shown schema contains no persistent track ID. The mixed-class video goal is appropriate for V1, but reviewed boxes for both classes and stable sequence-scoped IDs are needed for tracking metrics.
- No request was sent, no agreement was signed, and no data was acquired. **Owner action:** email the provider for the DUA and review/complete its process personally. After data is made available, confirm access and share the provider-authorized download method before acquisition. Never combine separate clips to fabricate a mixed scene.

## V2 — multiple drones

**Local annotations:** `datasets/external/Purdue_MultiTarget_UAV_2017/Video_Annotation-v2.zip`.

- File size: 955,489 bytes. SHA-256: `61581df9426c1827acfa8dfc1e4f930dfa265d9b6c2dcbfef0993f9d82f388d6`. ZIP CRC verification passed.
- Contains 50 refined annotation text files, `Clip_1` through `Clip_50`. Sample rows use frame, object ID, x, y, width, height. Sampled Clip 10 contains IDs 1–2; Clip 22 contains IDs 1–4 across sampled rows, with up to two simultaneous objects in sampled frames. IDs appear clip-scoped in these samples; full ID continuity and box/frame alignment have not been audited.
- The matching video archive is absent locally. No license, formal source URL, release version/date, or train/validation/test split was found in the local archive/project records.
- An official Purdue provider/project page with an active data/license/download record could not be verified through the available official site access during this review. Third-party repositories were not treated as authority for terms. No video download was attempted.
- **Disposition:** preserve the ZIP; do not score it, redistribute it, or acquire a video archive until the official owner/source, license/terms, split, and exact frame alignment are verified.

## Additional multi-drone candidate — Anti-UAV Track 3

- Official project page: <https://anti-uav.github.io/dataset/>. The official page describes Track 3 as multiple-UAV tracking with 200 training and 100 test sequences and frames containing 0 to over 40 targets. This is relevant to V2 tracking, not bird-vs-drone classification.
- The same page's training download link to ModelScope appears only in an HTML comment, not as an active link. The ModelScope record could not be verified for public-download status or license from the official project page. No license or usage terms were found there; the old test download links are also commented out.
- **Disposition:** candidate only; do not download until the current official training access method and license/terms are published and verified. Do not use challenge test data for local training/tuning or treat it as a public held-out set without explicit authorization.

## V3 — multiple birds

**Candidate:** SMOT4SB. Official baseline/challenge repository: <https://github.com/IIM-TTIJ/MVA2025-SMOT4SB>. Current dataset Drive link from its README: <https://drive.google.com/drive/folders/1Y1J13W6VlgDh-L28n_mVbs7HIfo_Hv5s?usp=sharing>.

- The official repo identifies SMOT4SB as small multi-object bird tracking and provides MOT-format frame, track ID, bounding-box, and bird-class fields. Its baseline-code `LICENSE` is MIT for code; that file is not the dataset license.
- The current public Drive README describes its contents as the Phase 1 public-test release and says dataset terms are in a `LICENSE` file in Drive. Commercial use requires contacting the organizers via <https://forms.gle/ytBUD6ELUu2vpqhRA>. The matching dataset license copy already present with the older local pilot is MIT-based, requires retaining attribution/copyright, and directs commercial use inquiries to the organizer. Reconcile that older pilot's source provenance against the current release before acquiring more files.
- The official test-set folder is not an eligible training acquisition. No additional SMOT4SB files were downloaded in this checkpoint.
- Four complete local pilot archives are documented as training-split sequences in `datasets/external/SMOT4SB_initial_train_pilot/pilot_subset_manifest.json`; their total is 772 frames, 4,337 bird boxes, and 45 sequence-local IDs. They remain development data, not independent evaluation. The 46 remaining training archives were not acquired; eight `.zip.part` files remain incomplete and must be preserved.
- **Disposition:** existing clips support bird-only MOT development. Before tuning, freeze sequence-level splits. Do not acquire the public-test bundle as training data.

## Conditional or out-of-scope candidates

### FBD-SV-2024

- Existing project notes say the share is privately gated/research-only. The dataset is not present locally. A current owner URL, exact terms, version, license, split, hashes, and access date are not recorded or verified.
- **Owner action:** do not request or use until the owner confirms eligibility, permitted use, and access. No contact or download was attempted.

### UB-SOD — image-only detector supplement

- Official Figshare record/API: <https://figshare.com/articles/dataset/UB-SOD_dataset_for_small_UAV_and_bird_detection_in_airport_low-altitude_surveillance/33733090> / <https://api.figshare.com/v2/articles/33733090>. Official benchmark repository: <https://github.com/DMCcodefinder/UB-SOD-benchmark>. Figshare v1 is public and non-embargoed, published 2026-09-14, and declares CC BY 4.0. Attribution, a license link, and indication of changes are required; the license permits sharing and adaptation, including commercial use.
- The provider describes 7,118 visible-light still images, 18,042 UAV/Bird boxes, approximately 32 airport environments, and fixed train/validation/test splits. Some images were extracted from video. Formats include YOLO, Pascal VOC, and COCO boxes; no persistent track IDs or source-video grouping are present in the acquired metadata.
- Acquired only the annotations archive, split/metadata archive, and checksum file into `datasets/external/UB-SOD_figshare_v1_verified_2026-10-22/`; original archives were retained and extracted copies placed in separate folders. Details and hashes are in its `acquisition_manifest.json`. Existing UB-SOD files elsewhere in the project were not overwritten.
- Validation: 7,118 YOLO labels and 7,118 XML files contain 18,042 boxes (10,047 UAV, 7,995 Bird); no malformed rows or invalid normalized coordinates/bounds were found. All three COCO image lists match the provider split files. Counts are train 5,765, validation 641, test 712, with zero image-ID overlap. All 7,118 provider image hashes are unique. The image archive (2,198,608,426 bytes) was not downloaded, so image readability and image-label pixel alignment remain unchecked.
- Split lists are image-level. No source video/environment mapping is available to prove sequence-level independence, so keep the test partition out of training/tuning and do not claim video/MOT or held-out V1/V2/V3 evidence. UB-SOD labels use `0=UAV, 1=Bird`, unlike project mapping `0=Bird, 1=Drone`; explicit remapping is required before detector use.

## Acquisition decision and follow-on data work

Only UB-SOD annotations/split metadata/checksum files were downloaded under a public, clearly stated CC BY 4.0 license; no image media was acquired. No gated access was attempted, and no incomplete transfer was removed. V1 remains DUA-gated; V2 video source/terms remain unverified; Anti-UAV Track 3 has no verified active training download/license; the current SMOT4SB public-test package is not suitable training data. The **requirements-freeze phase is complete** under the frozen acceptance specification in `docs/PHASE2_TEST_SCENARIOS.md`; V1/V2/V3 data readiness and scientific evaluation remain future work.

**Current project decision:** continue only with locally available data. Treat gated/unverified datasets (including WOSDETC, the Purdue video archive, and Anti-UAV Track 3) as future candidates. Do not contact a provider, sign terms, or attempt a gated download as part of this local-only phase. Revisit these candidates only when the project owner explicitly chooses to add them and their access terms are verified.
