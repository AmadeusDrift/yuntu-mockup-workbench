# PSD compatibility and stable release implementation plan

> **For agentic workers:** Use subagent-driven-development for the isolated adjustment implementation and sequential specification/quality reviews. The user explicitly authorized completing compatibility improvements, combining the local live-preview work, and publishing the new stable version. Continue through verification and publication without another approval gate.

**Goal:** Render common PSD adjustment effects, report known incompatibilities before running work, retain global artwork replacement and warm previews, and publish the combined tested release.

**Architecture:** Serialize supported adjustment parameters into compiled template operations. Fold adjustments over entirely static pixels into cache assets; evaluate effects that depend on artwork during rendering. Retain existing renderer lifetime/draft/full behavior. Store structured compatibility issues separately from approximation warnings and validate selected scenes before jobs.

**Tech Stack:** Existing Python/Pillow/NumPy/psd-tools, local HTTP service, plain JavaScript/Electron, existing signed update publisher and GitHub connector. No new runtime dependencies.

## Baseline

- [x] Inspect source and release records; remote latest remains `41e68d3980a0b699c10cb3cf0f3fb1d7b96a05f1` (0.3.7).
- [x] Preserve 93 current source/documentation files in `.analysis-temp/compatibility-baseline-20261110-162353`; retain all unpublished 0.3.8 changes.
- Local workspace has no `.git`. Remote source changes must use a checked parent commit or isolated checkout, never replace working files with 0.3.7.

## Task 1 — common adjustments

Owner: isolated implementer; files `workbench/core/psd_adjustments.py`, `workbench/tests/test_psd_adjustments.py`.

- [x] Add failing tests for `compile_adjustment(layer)` and `apply_adjustment_rgba(image, spec, mask=None, opacity=255)` before implementation.
- [x] Implement serialized RGB8 gradient maps, levels, curves, hue/saturation, exposure, invert, posterize and threshold. Keep existing brightness/contrast behavior reusable. Validate unsupported parameter variants instead of dropping them. Preserve input pixels/alpha, honor masks and opacity.
- [x] Use the bundled primary implementation/reference data and document relevant math/source references. Avoid adding SciPy or storing live Layer objects in cache files.
- [x] Validate identities, known color changes, transparency, masked adjustment, clipping caller behavior, invalid parameters, and actual supplied gradient settings.

## Task 2 — renderer integration and static reuse

Owner: root; files `workbench/core/engine.py`, `workbench/core/blend_modes.py`, `workbench/core/preview_renderer.py`, renderer tests.

- [x] Add failing integration cases containing an adjustment above a static clipping base and above a dynamic artwork slot.
- [x] Compile supported adjustment kinds to `{kind:'adjustment', adjustment:<kind>, spec:<serialized-spec>, mask:<asset>, opacity:<int>}` (preserve legacy brightness LUT operations).
- [x] Render the new operation using `apply_adjustment_rgba`; permit bounded-region optimization only for pointwise operations.
- [x] Fold purely static adjustment/clipping groups into immutable cached pixels without flattening artwork-dependent operations.
- [x] Extend common supported separable blend modes using the existing alpha-compositing rules and independent expected-color tests. Continue reporting complex unsupported styles/filters explicitly.
- [x] Verify draft rendering preserves adjustment semantics, native geometry, and exact full preview/export consistency.

## Task 3 — compatibility audit and job gating

Owner: root; files `workbench/compatibility.py`, `workbench/catalog.py`, `workbench/service.py`, `workbench/web/app.js`, `workbench/web/studio.css`, tests.

- [x] Add tests showing two unsupported features in one active scene are both reported, hidden/skip scenes do not falsely block, and known-incompatible jobs fail before task startup/output creation.
- [x] Introduce a versioned structured report `{version, key, status, issues:[{scene, scene_name, layer, message}], warnings, features}`. Keep approximation warnings separate; reuse old catalog data without assuming old warnings mean current support.
- [x] Audit active render paths for adjustments, blend modes, fills, filters, group effects, clipping and masks. Use the actual compiler for supported-variant validation.
- [x] Recheck existing imported templates with obsolete reports; apply selected scene modes/targets. Job validation remains authoritative even when clients are old.
- [x] Show usable/blocked status and concrete scene/layer reasons; disable only affected preview/export operations, keeping configuration/reimport/skip controls available. Retain whole-batch global settings and the current live-preview queue.

## Task 4 — real PSD and desktop acceptance

- [x] Create an isolated copy of the supplied black round-tag PSD; generate Photoshop reference renders when local automation is available, never save the source PSD or touch other open documents.
- [x] Verify all seven scenes and ten replaceable slots, mask boundaries, black metal/highlights, two distinct artwork replacements, exact preview/export pixels, source hashes, and warm draft/full performance.
- [x] Run the Python suite, frontend queue tests, updater/publisher tests, and relevant isolated desktop integration tests; add structured-compatibility UI/API coverage.
- [x] Perform independent specification and quality reviews; fix findings and rerun relevant checks.

## Task 5 — package, source sync and publish

- [x] Finalize 0.3.8 release notes, support matrix and verification records for the combined changes.
- [x] Build portable and installer, verify manifest file hashes, extract/test the installer in a clean isolated profile, and verify upgrade preservation from 0.3.7.
- [x] Publish source to the private repository using an expected parent SHA, retaining unrelated remote content; include verification records but no PSDs, artwork, tokens or signing keys.
- [x] Use the existing publisher to upload the installer, verify remote bytes/SHA-256, then publish the signed stable manifest. Verify public latest.json signature/version and installer digest.
- [x] Record deployed version and provide the published version and user update entry point.

Completed 2026-11-10: 0.3.8 stable published; public installer SHA-256 and Ed25519 signature verified, and the original 0.3.7 updater independently discovered/downloaded/verified the release. See workbench/updates/verification-0.3.8.md and release/*0.3.8*verification.json.
