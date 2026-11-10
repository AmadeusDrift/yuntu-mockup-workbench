# Continuous preview and artwork switching implementation plan

> **For agentic workers:** Use subagent-driven-development for the isolated UI task and sequential specification/quality reviews. The main agent owns backend integration and final desktop verification. The user has approved all five changes; continue without intermediate approval gates.

**Goal:** Keep the current PSD ready, show previews during edits, keep one batch-wide parameter set, switch artworks in the confirmation page, and never display superseded results as current.

**Architecture:** Retain one bounded, invalidatable renderer session. Use a smaller renderer for interactive frames and the existing exact renderer after settling. The UI keeps at most one task in flight plus the latest desired state. During uninterrupted editing, monotonic completed drafts from the same artwork, template, scene and fitting context may be displayed as updating; only exact matching results are current. Settled full frames require exact settings and task identity. Batch export continues to use one settings snapshot for every artwork.

**Tech Stack:** Existing Python/Pillow/NumPy engine, local HTTP service, plain JavaScript/CSS, Electron. No new dependencies.

## Baseline and scope

- [x] Inspect local source, 0.3.7 release/verification records and GitHub commit `41e68d3`.
- [x] Preserve 74 source files in `.analysis-temp/live-preview-baseline-20261110-150201` with SHA-256 inventory.
- No `.git` exists in this workspace. Do not reset, replace, publish or initialize repository history.
- Keep global scale/offset/brightness settings. Do not add artwork overrides, undo, side-by-side comparison or manipulation on the right-hand image.

## Task 1 — renderer lifetime and source validation

Files: `workbench/preview_session.py`, `workbench/cache.py`, `workbench/service.py`, `workbench/tests/test_preview_session.py`.

- [x] Add failing tests using a cached synthetic template and two different artworks: repeated editor/preview calls hash the unchanged PSD once and construct one full renderer; changed PSDs are rejected; changed manifests/settings invalidate the session; cache cleaning and closure release it.
- [x] Remember a verified file identity (resolved path, device/inode, size, modification and change times) for interactive checks. A new session or changed identity performs a full hash. Batch export always performs a fresh full check.
- [x] Retain only the current renderer, pin its disk cache for its lifetime, count nested pins, and bound retained decoded memory. Oversized sessions can render but must be released after use.
- [x] Allow preference writes while editor/preview jobs run, since each job already owns a settings snapshot. Preserve batch locking.
- [x] Run focused tests and then existing service/cache regression tests.

## Task 2 — draft previews and lightweight task polling

Files: `workbench/core/engine.py`, `workbench/preview_session.py`, `workbench/service.py`, `workbench/server.py`, renderer/session tests.

- [x] Add failing tests for draft dimensions, original renderer immutability, placement/alpha/brightness and a warp fixture; full previews remain identical to full exports.
- [x] Produce a bounded-size renderer from decoded assets before rendering, scaling canvas/native geometry, masks, shadows and normalized warp maps consistently. Full rendering stays on the existing path.
- [x] Extend `POST /api/preview` with optional boolean `draft` (default false). Draft mode requires one scene. Return the existing `task_id`; completed task adds `preview_quality: 'draft' | 'full'` and keeps existing preview source/settings/scene metadata.
- [x] Add `GET /api/task` returning `{task, snapshot_id}` without catalog/cache scans. `/api/state` also includes a monotonic `snapshot_id` so late snapshots cannot revert task state.
- [x] Close retained session resources during shutdown; invalidate before cache cleanup and template reconfiguration/removal.

## Task 3 — continuous editor and confirmation-page navigator

Files: `workbench/web/app.js`, `workbench/web/studio.css`, optional focused UI module; `workbench/desktop/test-live-preview.cjs` and runner.

- [x] Add a failing regression for a right preview arriving before pointer release, final full quality, rapid source A→B→C without stale display, global parameters surviving navigation, and navigation over a 40-item page boundary.
- [x] Keep editor interaction and artwork navigation enabled during automatic editor/preview jobs; continue blocking destructive operations and batch starts until appropriate.
- [x] Submit draft frames while dragging/range input changes, retain only the newest pending parameters, and automatically request a full frame after settling. Use lightweight task polling while a frame is in flight.
- [x] Reuse unbrightened editor artwork geometry for brightness changes and keep the left canvas responsive; prepare geometry only when source/fit/orientation/scene changes.
- [x] Add confirmation-page thumbnails, named previous/next buttons, file name and index/total. Preserve selected scene, global settings and view state when switching; auto-preview the new source.
- [x] Match source/template/scene/context/task before adopting a moving draft, and exact settings before marking it current or adopting full quality. Keep the last usable image visibly labelled as updating until the newest result is loaded. Avoid replacing editor DOM during pointer capture or typing.
- [x] Clearly state that settings apply to all batch artworks and that the navigator only chooses a preview sample.

## Task 4 — integration, verification and delivery

- [x] Run `runtime/python.exe -I -B workbench/tests/run.py`, relevant Node checks and existing desktop regressions.
- [x] Build an isolated local desktop test copy using source plus the existing runtime; exercise native dragging, scale/brightness inputs, previous/next pagination switching, rapid changes, exact settling, preference persistence and shutdown.
- [x] On the existing real seven-scene fixtures, compare full preview pixels with export, measure warm repeated preview time and retained memory, and verify source files are unchanged.
- [x] Review scope compliance, then code quality; fix findings and rerun affected checks.
- [x] Record results in `workbench/updates/verification-live-preview.md` with release status explicitly unpublished. Provide a working local entry point; do not publish the stable update feed.
