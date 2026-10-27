# ASUS Contest Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Deliver one reproducible historical comparison and matching English submission materials.
**Architecture:** Reuse the existing local adapter and interface. Add a deterministic record CLI outside the frozen research code. Media consumes its verified record and actual interface captures.
**Tech Stack:** Existing Python, JavaScript/Node, local presentation tooling and FFmpeg.
**Spec:** `docs/superpowers/specs/2026-10-27-contest-closeout-design.md`

## Global Constraints
Canonical case2026-08-24/06:45/07:30, advance15, delay30, minimum5, cost0.5. Changed deadline07:31. Five candidates, zero formal recommendations, unknown full lateness-risk difference. No new source requests, training, model forward, evaluation, timer changes, raw/truth/weight publication, deployment or public video upload. English PDF<=20 main pages and<15MB. Video<=180seconds. Preserve all original checkout work.

## Review Focus
- Hidden historical outcomes must not leak into undisclosed records.
- Changed deadline must not reuse D45 on-time probabilities.
- Numeric condition matches must not become recommendations.
- Null prediction/measurements must remain unknown rather than zero.
- All media figures and dates must match the published replay case and version.

### Task1: Reproducible comparison record
Files: create `scripts/contest_replay.cjs`, `tests/test_contest_replay.py`, and `docs/contest/replay/*.json`; modify `README.md`, `docs/reproducibility.md`, `CHANGELOG.md` only for this deliverable.
Consumes existing `DepartureApi.options(query)` and `.outcomes(options)` from public adapter. Produces deterministic JSON for undisclosed/disclosed/deadline-changed case with adapter SHA, full five rows and explicit unknowns. CLI: `node scripts/contest_replay.cjs --scenario undisclosed|disclosed|changed-deadline`; JSON to stdout. Do not modify existing app/model logic unless a verified defect prevents the required task.
- [ ] Add failing behavior tests for canonical inputs/five shifts, zero matches at minimum5, zero recommendations, disclosure boundaries,07:31 null outcomes, forecast preservation and deterministic bytes. Check expected arithmetic independently.
- [ ] Run tests and observe the missing-command failure.
- [ ] Implement smallest offline CLI and reject unknown scenarios.
- [ ] Generate three fixed records, document exact commands and inspect diff for private paths/data.
- [ ] Run public unittest suite and existing Node contract. Report exact commands and any baseline failure. Do not execute research jobs or hidden-data tests.
- [ ] Self-review and hand off for independent review. Controller performs commits.

### Task2: English deck and demonstration video
Files: local deliverables under task workspace `deliverables/`; publishable source/copy under `docs/contest/` only after review. Do not change Task1 files.
Consumes Task1 record plus actual public interface captures; existing16-page deck is a reference. Produces editable PPTX, PDF<15MB, MP4<=180seconds, subtitles/transcript, and brief artifact validation record.
- [ ] Read relevant presentation skill, existing source and gap analysis. Establish slide content around the user task, scope, model/evidence distinction, feasibility and Stage II validation plan.
- [ ] Replace9/24 media example with8/24. Use actual UI evidence, not fabricated controls or values.
- [ ] Update36/24 delivery status and failed original truth gate, preserve unverified hardware and effect boundaries.
- [ ] Render and inspect every slide; fix overflow/content errors. Verify PDF page count and size.
- [ ] Create video showing setup/comparison/refusal/disclosure/changed-deadline, using readable English subtitles. Verify duration, codecs, audio presence and representative frames. State if silent/edited.
- [ ] Hand off all files with sizes, hashes and validation evidence for independent review.

### Task3: Review, package, deliver and integrate
Files: `docs/contest/README.md`, short submission checklist, package manifest, task-private QA ledger.
- [ ] Independently compare records, UI and media; complete justified tests only.
- [ ] Confirm no GitHub push/merge deployment trigger, review exact allowlist and sensitive-data scan.
- [ ] Resolve important review findings, commit scoped files, push branch and create/attach PR. Merge only after exact-head checks and approvals required by repository pass.
- [ ] Save validated PDF/video/PPTX to Library using current prepared upload helper. Inspect every result/xattr and report exact Library IDs.
- [ ] Provide replay command, precise Git evidence and remaining human steps: platform/format, consent and official submission/public video.
