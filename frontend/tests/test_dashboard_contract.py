import re
import unittest
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[1]


class DashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = (FRONTEND_ROOT / "app.js").read_text(encoding="utf-8")
        cls.index_html = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")

    def test_motion_detail_renders_requested_metrics_and_pixel_units(self):
        required_fragments = (
            "detailStat('Track ID', track.track_id)",
            "detailStat('Class', track.class)",
            "detailStat('Motion state', trajectory.motion_state)",
            "px/s",
            "deg`",
            "px/s²",
            "detailStat('Path length'",
            "detailStat('Net displacement', `${formatNumber(motionSummary.net_displacement_px, 1)} px`)",
            "detailStat('Straightness ratio'",
            "detailStat('Direction consistency'",
            "detailStat('Trajectory duration'",
            "detailStat('Observation count'",
            "detailStat('Motion quality'",
            "Image-plane motion metrics",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.app_js)

    def test_vlm_ui_uses_explicit_execution_status(self):
        self.assertIn("run?.vlm_status", self.app_js)
        self.assertIn("completed: 'complete'", self.app_js)
        self.assertIn("processing: 'active'", self.app_js)
        self.assertIn("failed: 'failed'", self.app_js)
        self.assertIn("skipped: 'skipped'", self.app_js)
        self.assertIn("vlmStatus === 'unknown'", self.app_js)
        self.assertIn('data-stage="vlm"', self.index_html)

    def test_dashboard_orders_persistent_player_pipeline_phase_and_results(self):
        order = (
            self.index_html.index('class="monitor-workspace"'),
            self.index_html.index('id="run-section"'),
            self.index_html.index('id="phase-detail-area"'),
            self.index_html.index('id="results-section"'),
        )
        self.assertEqual(order, tuple(sorted(order)))
        self.assertIn('<video id="input-video-player" controls', self.index_html)
        self.assertEqual(self.index_html.count('class="stage-button"'), 10)
        for phase in (3, 4, 5, 6):
            self.assertIn(f'id="phase{phase}-panel"', self.index_html)
        phase_panels = self.index_html[
            self.index_html.index('id="phase-detail-area"'):
            self.index_html.index('id="results-section"')
        ]
        self.assertEqual(
            len(re.findall(r'<section id="phase[3-6]-panel"[^>]* hidden>', phase_panels)),
            4,
        )
        self.assertIn("selectPipelineStage(button.dataset.stageTarget)", self.app_js)
        self.assertIn("panel.hidden = panel.id !== phasePanel?.slice(1) || !state.phase3Input", self.app_js)

    def test_video_seek_reads_stored_tracking_results_without_inference(self):
        self.assertIn('id="current-frame-detection-details"', self.index_html)
        self.assertIn("Frame Index", self.index_html)
        self.assertIn("Total Frames", self.index_html)
        self.assertIn("Math.floor(player.currentTime * metadata.fps) + 1", self.app_js)
        self.assertIn("result.total_frames", self.app_js)
        self.assertIn("state.alertEvidence?.frame_index === result.frame_index", self.app_js)
        self.assertIn(": 'Analyzed'", self.app_js)
        self.assertIn("? formatVideoTimestamp(player.currentTime)", self.app_js)
        self.assertIn("Tracked Objects:", self.app_js)
        self.assertIn("No tracked objects in this frame.", self.app_js)
        self.assertIn("variable-frame-rate video", self.index_html)
        self.assertIn("api(\n      `/api/runs/${encodeURIComponent(state.runId)}/frames/${frameIndex}`", self.app_js)
        self.assertIn("$('#input-video-player').addEventListener('timeupdate', () => {", self.app_js)
        self.assertIn("$('#input-video-player').addEventListener('seeked', () => {", self.app_js)
        seek_flow = self.app_js[
            self.app_js.index("async function refreshStoredFrameObservations()"):
            self.app_js.index("function showProcessedFrame(")
        ]
        self.assertNotIn("/api/phase4/detect", seek_flow)
        self.assertNotIn("detectNextPhase4Frame", seek_flow)
        self.assertIn("state.run?.status !== 'completed'", seek_flow)

    def test_manual_frame_controls_are_labeled_debug_only(self):
        for phase in range(3, 7):
            with self.subTest(phase=phase):
                self.assertIn(f"OPTIONAL DEBUG · PHASE {phase}", self.index_html)
        self.assertIn("Use Start Analysis for normal uploaded-video processing.", self.index_html)

    def test_direct_phase_responses_update_only_their_own_pipeline_stages(self):
        for phase, next_phase, stage in (
            ("detectNextPhase4Frame", "trackNextPhase5Frame", "detection"),
            ("trackNextPhase5Frame", "resetPhase5Tracking", "tracking"),
            ("updatePhase6Temporal", "resetPhase5Tracking", "temporal"),
        ):
            with self.subTest(phase=phase):
                start = self.app_js.index(f"async function {phase}(")
                end = self.app_js.index(f"async function {next_phase}(", start)
                flow = self.app_js[start:end]
                self.assertIn(f"setPhaseExecution('{stage}', 'complete')", flow)
                self.assertIn(f"setPhaseExecution('{stage}', 'failed')", flow)
        pipeline = self.app_js[
            self.app_js.index("function renderPipeline("):
            self.app_js.index("function setPhaseExecution(")
        ]
        self.assertIn("const directStatus = state.phaseExecution[stage]", pipeline)
        self.assertIn("run?.status === 'completed'", pipeline)
        self.assertNotIn("setPhaseExecution('completed'", self.app_js)

    def test_api_calls_do_not_rebind_dashboard_controls(self):
        api_function = self.app_js[
            self.app_js.index("async function api("):
            self.app_js.index("function escapeHTML(")
        ]
        self.assertNotIn("addEventListener", api_function)
        self.assertIn("$('#pipeline-stages').addEventListener", self.app_js)
        self.assertIn("$('#generate-report').addEventListener", self.app_js)

    def test_run_history_uses_existing_run_api_and_shared_pagination(self):
        self.assertIn('href="/history"', self.index_html)
        self.assertIn('id="history-section"', self.index_html)
        self.assertIn('id="history-rows-per-page"', self.index_html)
        self.assertIn('id="history-pagination"', self.index_html)
        self.assertIn("api('/api/runs')", self.app_js)
        self.assertIn("api('/api/auth/history')", self.app_js)
        self.assertIn('data-history-tab="analysis"', self.index_html)
        self.assertIn('data-history-tab="session"', self.index_html)
        self.assertIn('id="session-history-rows"', self.index_html)
        self.assertIn("renderPageNavigation($('#history-pagination')", self.app_js)
        self.assertIn("No runs are available for this account.", self.app_js)
        self.assertIn("renderPageNavigation($('#alert-pagination')", self.app_js)
        self.assertIn('id="alert-rows-per-page"', self.index_html)
        self.assertIn("report?.alert && typeof report.alert === 'object'", self.app_js)

    def test_results_library_reuses_stored_runs_without_reprocessing(self):
        self.assertIn('id="results-run-cards"', self.index_html)
        self.assertIn("function renderResultsRunCards()", self.app_js)
        self.assertIn("View stored results", self.app_js)
        self.assertIn("Fresh re-analysis", self.app_js)
        self.assertIn("/reuse-candidates", self.app_js)
        self.assertIn("/reanalyze", self.app_js)
        self.assertIn("Check resume", self.app_js)
        self.assertIn("/resume-checkpoint", self.app_js)
        self.assertIn("/resume`", self.app_js)
        self.assertIn("Validated detector/tracker results were reused", self.app_js)
        self.assertIn('id="video-trail-overlay"', self.index_html)
        self.assertIn("function drawVideoTrailOverlay()", self.app_js)
        self.assertIn("restoreRunVideo(run)", self.app_js)

    def test_processing_run_polls_for_positive_early_alerts(self):
        self.assertIn(
            "if (run.status === 'processing' && run.input_type !== 'thermal') await pollEarlyUavAlert(runId)",
            self.app_js,
        )
        self.assertIn("`/api/runs/${encodeURIComponent(runId)}/early-alert`", self.app_js)
        self.assertIn(
            "result.early_alert.uav_alert === true && result.early_alert.state === 'provisional'",
            self.app_js,
        )
        self.assertIn("alert_track_ids: result.alert_track_ids", self.app_js)
        self.assertIn("alert_status: result.alert_status", self.app_js)

    def test_dashboard_and_video_analysis_symbols_are_utf8_safe_and_accessible(self):
        self.assertNotRegex(
            self.app_js,
            r"ðŸ|â(?:€|€“|€”|†|œ|š|—|€¢|…|€¦)|Ã—|Â[·²]",
        )
        self.assertIn("playing ? '⏸' : '▶'", self.app_js)
        self.assertIn('id="theme-toggle-sun"', self.index_html)
        self.assertIn('id="theme-toggle-moon"', self.index_html)
        self.assertIn("Switch to ${nextTheme} mode", self.app_js)
        self.assertIn('class="assessment-symbol" aria-hidden="true"', self.app_js)

    def test_stop_analysis_is_processing_only_and_stops_run_polling(self):
        self.assertIn(
            '<button id="stop-analysis" class="button button-danger stop-analysis-button" type="button" hidden>',
            self.index_html,
        )
        self.assertIn("stopButton.hidden = run.status !== 'processing'", self.app_js)
        self.assertIn("`/api/runs/${encodeURIComponent(runId)}/cancel`", self.app_js)
        self.assertIn("method: 'POST'", self.app_js)
        self.assertIn("button.textContent = 'Stopping…'", self.app_js)
        self.assertIn("'Analysis Cancelled. Choose New analysis to prepare another video.'", self.app_js)
        self.assertIn("if (state.cancelPending) return", self.app_js)
        self.assertIn("$('#stop-analysis').addEventListener('click', stopAnalysis)", self.app_js)
        status_update = self.app_js[
            self.app_js.index("async function updateRunStatus("):
            self.app_js.index("async function pollRun(", self.app_js.index("async function updateRunStatus("))
        ]
        self.assertLess(
            status_update.index("stopButton.hidden = run.status !== 'processing'"),
            status_update.index("if (run.status === 'prepared')"),
        )

    def test_reliability_phase_renders_real_reliability_scores(self):
        renderer = self.app_js[
            self.app_js.index("function renderTrackPhaseContent("):
            self.app_js.index("function renderAssessmentPhaseDetail(")
        ]
        self.assertIn("stage === 'reliability'", renderer)
        for field in (
            "reliability.confidence_score",
            "reliability.consistency_score",
            "reliability.persistence_score",
            "reliability.motion_score",
            "reliability.reliability_score",
            "reliability.status",
            "reliability.frames_seen",
        ):
            with self.subTest(field=field):
                self.assertIn(field, renderer)
        self.assertIn("<h3>Reliability Analysis</h3>", renderer)

    def test_evidence_phase_renders_existing_selection_and_thumbnails(self):
        renderer = self.app_js[
            self.app_js.index("function renderTrackPhaseContent("):
            self.app_js.index("function renderAssessmentPhaseDetail(")
        ]
        for field in ("detail.selected_evidence", "item.frame", "item.selection_group", "item.crop_path", "evidenceImageUrl(item.crop_path)", "'Selection Reason'"):
            with self.subTest(field=field):
                self.assertIn(field, renderer)
        self.assertIn("'Early Segment'", renderer)
        self.assertIn("'Middle Segment'", renderer)
        self.assertIn("'Late Segment'", renderer)

    def test_vlm_phase_renders_only_reported_verification_results(self):
        renderer = self.app_js[
            self.app_js.index("function renderTrackPhaseContent("):
            self.app_js.index("function renderAssessmentPhaseDetail(")
        ]
        for field in ("vlm.available", "result.label", "result.confidence", "result.reason", "result.frame"):
            with self.subTest(field=field):
                self.assertIn(field, renderer)
        self.assertIn("'Verification Status', 'Completed'", renderer)

    def test_fusion_phase_renders_existing_fusion_contract(self):
        renderer = self.app_js[
            self.app_js.index("function renderTrackPhaseContent("):
            self.app_js.index("function renderAssessmentPhaseDetail(")
        ]
        for field in (
            "fusion.detector_evidence?.class",
            "fusion.detector_evidence?.mean_confidence",
            "fusion.reliability?.score",
            "vlmResult?.label",
            "vlmResult?.confidence",
            "fusion.fusion_mode",
            "fusion.final_label",
            "fusion.final_confidence",
        ):
            with self.subTest(field=field):
                self.assertIn(field, renderer)
        self.assertIn("<h3>Evidence Fusion</h3>", renderer)

    def test_assessment_phase_renders_existing_run_summary_data(self):
        renderer = self.app_js[
            self.app_js.index("function renderAssessmentPhaseDetail("):
            self.app_js.index("function renderCompletedPhaseDetail(")
        ]
        for field in ("state.overview.total_tracks", "counts.UAV", "counts.Bird", "state.overview.uncertain_tracks", "averageReliability", "state.run.status", "state.report?.alert?.message"):
            with self.subTest(field=field):
                self.assertIn(field, renderer)

    def test_phase_specific_pending_states_and_completed_report_are_explicit(self):
        for message in (
            "Waiting for sufficient temporal observations.",
            "Evidence selection is pending reliability analysis.",
            "Automatic VLM verification is disabled. Detector-only class labels remain unverified.",
            "Fusion is pending reliability and verification results.",
            "Final assessment is pending fusion.",
            "Run completion is pending.",
        ):
            with self.subTest(message=message):
                self.assertIn(message, self.app_js)
        completed = self.app_js[
            self.app_js.index("function renderCompletedPhaseDetail("):
            self.app_js.index("async function updateRunStatus(")
        ]
        self.assertIn("state.report?.tracks", completed)
        self.assertIn("'Report Availability', 'Available'", completed)
        self.assertIn("if (state.run?.status !== 'completed')", completed)

    def test_phase_details_do_not_invent_analysis_values(self):
        renderer = self.app_js[
            self.app_js.index("function renderTrackPhaseContent("):
            self.app_js.index("function renderCompletedPhaseDetail(")
        ]
        self.assertIn("function phaseScore(value)", self.app_js)
        self.assertIn("value === null || value === undefined ? 'N/A'", self.app_js)
        self.assertIn("if (!evidence.length)", renderer)
        self.assertIn("if (!vlm.available || !vlmResults.length)", renderer)
        self.assertIn("if (!fusion.final_label)", renderer)
        self.assertNotRegex(renderer, r"(?:Confidence|Score|Reliability)[^\\n]{0,80}\\b0\\.\\d+")

    def test_video_player_is_persistent_across_phase_selection(self):
        self.assertEqual(self.index_html.count('id="input-video-player"'), 1)
        selector = self.app_js[
            self.app_js.index("function selectPipelineStage("):
            self.app_js.index("const PHASE_EMPTY_MESSAGES")
        ]
        self.assertIn("renderTrackPhaseDetail(stage)", selector)
        self.assertNotIn("input-video-player", selector)
        self.assertNotIn("removeAttribute('src')", selector)
        self.assertIn('controls playsinline', self.index_html)

    def test_source_prepared_and_model_resolutions_are_labeled_separately(self):
        for fragment in (
            '<dt>Original Video</dt>',
            '<dt>Prepared Frame</dt>',
            '<dt>Model Input</dt>',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.index_html)
        self.assertIn("state.preparedFrameResolution = { width: result.width, height: result.height }", self.app_js)
        self.assertIn("metadata.model_input_width", self.app_js)
        self.assertIn("metadata.model_input_height", self.app_js)
        self.assertIn("result.width, height: result.height", self.app_js)

    def test_switching_runs_clears_prepared_resolution_and_new_frames_replace_it(self):
        clear_results = self.app_js[
            self.app_js.index("function clearRunResults("):
            self.app_js.index("function showSelectedFile(", self.app_js.index("function clearRunResults("))
        ]
        open_run = self.app_js[
            self.app_js.index("async function openHistoryRun("):
            self.app_js.index("function changePage(", self.app_js.index("async function openHistoryRun("))
        ]
        acquire_frame = self.app_js[
            self.app_js.index("async function acquireNextPhase3Frame("):
            self.app_js.index("async function detectNextPhase4Frame(", self.app_js.index("async function acquireNextPhase3Frame("))
        ]
        self.assertIn("state.preparedFrameResolution = null", clear_results)
        self.assertIn("navigateTo(`/results/${encodeURIComponent(runId)}`)", open_run)
        route_selection = self.app_js[
            self.app_js.index("async function renderCurrentRoute("):
            self.app_js.index("function setEntryView(")
        ]
        self.assertIn("localStorage.setItem(RUN_STORAGE_KEY, route.runId)", route_selection)
        self.assertIn("state.preparedFrameResolution = { width: result.width, height: result.height }", acquire_frame)

    def test_phase_one_entry_and_authenticated_shell_are_present(self):
        self.assertIn('id="splash-view"', self.index_html)
        self.assertIn('id="login-view"', self.index_html)
        self.assertIn('id="login-form"', self.index_html)
        self.assertIn('id="dashboard-view"', self.index_html)
        self.assertIn('id="logout-button"', self.index_html)
        self.assertIn('id="dashboard-total-objects"', self.index_html)
        self.assertIn('id="dashboard-drones"', self.index_html)
        self.assertIn('id="dashboard-birds"', self.index_html)
        self.assertIn('id="dashboard-uncertain"', self.index_html)
        self.assertIn('autocomplete="username"', self.index_html)
        self.assertIn('autocomplete="current-password"', self.index_html)
        for item in ("Dashboard", "Live CCTV", "Video Analysis", "Results", "History", "Reports", "Settings"):
            with self.subTest(item=item):
                self.assertIn(item, self.index_html)
        self.assertIn("btf-current-run-id", self.app_js)
        self.assertIn("api('/api/auth/session')", self.app_js)
        self.assertIn("api('/api/auth/login'", self.app_js)
        self.assertIn("api('/api/auth/logout'", self.app_js)
        self.assertIn("btf-auth-required", self.app_js)
        logout_flow = self.app_js[
            self.app_js.index("$('#logout-button').addEventListener"):
            self.app_js.index("window.addEventListener('btf-auth-required'")
        ]
        self.assertNotIn("localStorage.removeItem", logout_flow)
        self.assertIn("@keyframes drone-emerge", (FRONTEND_ROOT / "styles.css").read_text(encoding="utf-8"))
        for filename in ("app.js", "index.html"):
            source = (FRONTEND_ROOT / filename).read_text(encoding="utf-8")
            self.assertNotIn("admin@123", source)

    def test_major_navigation_uses_dedicated_same_tab_routes(self):
        routes = {
            "dashboard": "/dashboard",
            "cctv": "/live-cctv",
            "video-analysis": "/video-analysis",
            "results": "/results",
            "history": "/history",
            "alerts": "/alerts",
            "reports": "/reports",
            "settings": "/settings",
        }
        for navigation, route in routes.items():
            with self.subTest(navigation=navigation):
                self.assertRegex(
                    self.index_html,
                    rf'<a class="nav-link[^"]*" href="{re.escape(route)}" data-navigation="{navigation}"',
                )
        self.assertIn("history.pushState(routeState, '', targetPath)", self.app_js)
        self.assertIn("window.addEventListener('popstate'", self.app_js)
        self.assertNotIn('target="_blank"', self.index_html)
        self.assertNotIn("window.open(", self.app_js)
        self.assertIn('data-route-page="result-detail"', self.index_html)
        self.assertIn('data-route-page="report-detail"', self.index_html)
        sidebar = self.index_html[self.index_html.index('<nav>'):self.index_html.index('</nav>')]
        self.assertIn('href="/video-analysis"', sidebar)
        self.assertIn('href="/reports"', sidebar)
        self.assertIn('data-route-page="video-analysis"', self.index_html)
        self.assertIn('data-route-page="reports"', self.index_html)
        self.assertIn('data-route-link="/results"', self.index_html)
        self.assertIn('data-route-link="/reports"', self.index_html)

    def test_phase_two_source_and_modality_flow_stops_before_pipeline(self):
        for fragment in (
            'id="source-upload"',
            'id="cctv-input"',
            'id="rtsp-url"',
            'id="test-cctv"',
            'id="start-cctv"',
            'id="stop-cctv"',
            'data-input-type="rgb"',
            'data-input-type="thermal"',
            'data-input-type="radar"',
            'id="prepare-input"',
            'id="phase3-panel"',
            'id="phase3-acquire"',
            'id="phase3-stop"',
            'id="phase3-frame-index"',
            'id="phase3-preprocessing"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.index_html)
        prepare_flow = self.app_js[
            self.app_js.index("async function beginAnalysis()"):
            self.app_js.index("async function startPreparedAnalysis()")
        ]
        self.assertIn("prepareUploadedInput(file, state.inputType)", prepare_flow)
        self.assertNotIn("/api/runs/${encodeURIComponent(created.run_id)}/start", prepare_flow)
        self.assertIn("request.open('POST', '/api/input/prepare')", self.app_js)
        self.assertIn("cctvReference: null", self.app_js)
        self.assertIn("result.live_reference_id", self.app_js)
        self.assertIn("READY_FOR_PHASE_3", self.app_js)
        self.assertIn("async function startPreparedAnalysis()", self.app_js)
        self.assertIn("api('/api/phase3/frame'", self.app_js)
        self.assertIn("READY_FOR_PHASE_4", self.app_js)
        self.assertIn("project does not define a supported Radar upload format", self.app_js)
        self.assertIn("Analysis has not been started.", prepare_flow)

    def test_video_analysis_is_upload_only_and_live_cctv_has_its_own_route(self):
        video_analysis = self.index_html[
            self.index_html.index('id="video-analysis"'):
            self.index_html.index('id="live-cctv"')
        ] if 'id="live-cctv"' in self.index_html else self.index_html[
            self.index_html.index('id="video-analysis"'):
            self.index_html.index('data-route-page="video-analysis"')
        ]
        self.assertNotIn('id="source-cctv"', video_analysis)
        self.assertIn('id="source-upload"', video_analysis)
        self.assertIn('id="cctv-input"', self.index_html)
        self.assertIn("moveRouteNode('#cctv-input', '#live-cctv-slot')", self.app_js)
        self.assertIn("await selectInputSource('upload')", self.app_js)

    def test_dashboard_trends_use_real_backend_data_and_date_filtered_results(self):
        self.assertIn('id="sector-dashboard-detection-trend"', self.index_html)
        self.assertIn('id="sector-dashboard-alert-trend"', self.index_html)
        self.assertIn('id="hq-dashboard-trends"', self.index_html)
        self.assertIn("api('/api/headquarters/dashboard-trends')", self.app_js)
        self.assertIn("api('/api/sector/dashboard')", self.app_js)
        self.assertIn("data-dashboard-date", self.app_js)
        self.assertIn("navigateTo(`/results?date=", self.app_js)
        self.assertIn('id="results-date-filter"', self.index_html)

    def test_phase_four_detection_ui_is_single_frame_and_phase_scoped(self):
        for fragment in (
            'id="thermal-debug-navigation"',
            'id="phase4-panel"',
            'id="phase4-detect"',
            'id="phase4-model"',
            'id="phase4-inference"',
            'id="phase4-count"',
            'id="phase4-frame"',
            'id="phase4-results"',
            'id="phase4-preview"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.index_html)
        self.assertIn("api('/api/phase4/detect'", self.app_js)
        self.assertIn("supportsSingleFrameDetectionInput(input)", self.app_js)
        self.assertIn("['rgb', 'thermal']", self.app_js)
        self.assertIn("async function detectNextPhase4Frame()", self.app_js)
        detect_flow = self.app_js[
            self.app_js.index("async function detectNextPhase4Frame()"):
            self.app_js.index("async function trackNextPhase5Frame()")
        ]
        self.assertNotIn("track_id", detect_flow)
        self.assertNotIn("reliability", detect_flow)
        self.assertNotIn("vlm", detect_flow)

    def test_phase_five_tracking_ui_stays_within_tracking_contract(self):
        for fragment in (
            'id="phase5-panel"',
            'id="phase5-track"',
            'id="phase5-reset"',
            'id="phase5-status"',
            'id="phase5-count"',
            'id="phase5-results"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.index_html)
        self.assertIn("api('/api/phase5/track'", self.app_js)
        self.assertIn("api('/api/phase5/reset'", self.app_js)
        tracking_flow = self.app_js[
            self.app_js.index("async function trackNextPhase5Frame()"):
            self.app_js.index("async function resetPhase5Tracking()")
        ]
        self.assertIn("track.track_id", tracking_flow)
        self.assertIn("input.input_type !== 'thermal'", tracking_flow)
        self.assertNotIn("reliability", tracking_flow)
        self.assertNotIn("vlm", tracking_flow)
        self.assertIn("updatePhase6Temporal(result)", tracking_flow)

    def test_phase_six_temporal_ui_uses_separate_authenticated_handoff(self):
        for fragment in (
            'id="phase6-panel"',
            'id="phase6-status"',
            'id="phase6-frames"',
            'id="phase6-timing"',
            'id="phase6-results"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.index_html)
        self.assertIn("api('/api/phase6/temporal'", self.app_js)
        self.assertIn("CCTV observation sequence · time/FPS unavailable", self.app_js)

    def test_fresh_login_resets_only_the_current_dashboard_run(self):
        login_view = self.app_js[
                self.app_js.index("function showLogin("):
                self.app_js.index("function showDashboard(")
        ]
        reset = self.app_js[
                self.app_js.index("function resetDashboardRun("):
                self.app_js.index("async function resumeCurrentRun(")
        ]
        logout = self.app_js[
                self.app_js.index("$('#logout-button').addEventListener"):
                self.app_js.index("window.addEventListener('btf-auth-required'")
        ]

        self.assertIn("resetDashboardRun();", login_view)
        self.assertIn("showLogin();", logout)
        self.assertIn("localStorage.removeItem(RUN_STORAGE_KEY)", reset)
        self.assertNotIn("localStorage.clear(", reset)
        self.assertNotIn("PLAYED_HIGH_ALERTS_STORAGE_KEY", reset)
        self.assertIn("clearRunResults();", reset)
        self.assertIn("resetUpload();", reset)
        self.assertIn("renderPipeline({ status: 'idle', stage: 'uploaded' })", reset)
        self.assertIn("state.report = null;", self.app_js)
        self.assertIn("renderAlerts(null);", self.app_js)
        self.assertIn("$('#results-section').hidden = true;", self.app_js)

    def test_new_runs_and_history_selection_remain_current_run_flows(self):
        prepare_flow = self.app_js[
                self.app_js.index("async function beginAnalysis("):
                self.app_js.index("function renderPipeline(")
        ]
        history_flow = self.app_js[
                self.app_js.index("async function loadHistory("):
                self.app_js.index("function changePage(")
        ]
        initialization = self.app_js[
                self.app_js.index("async function initializeDashboard("):
                self.app_js.index("async function initialize(")
        ]

        self.assertIn("localStorage.setItem(RUN_STORAGE_KEY, state.runId)", prepare_flow)
        self.assertIn("api('/api/runs')", history_flow)
        self.assertIn("navigateTo(`/results/${encodeURIComponent(runId)}`)", history_flow)
        self.assertIn("await resumeCurrentRun(route.runId)", self.app_js)
        self.assertIn("await resumeCurrentRun();", initialization)


if __name__ == "__main__":
    unittest.main()
