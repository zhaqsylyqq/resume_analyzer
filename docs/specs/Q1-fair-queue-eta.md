# Q1 — Fair queue between sessions and time remaining

## Goal
A big batch (200 resumes) must not hold up a small one (5 resumes) started a minute later,
and recruiters should see roughly how long the analysis will take.

## Fair queue
- One process-wide pool of `GEMINI_CONCURRENCY` worker threads replaces the worker thread
  per session. Sessions with queued resumes form a rotation.
- A worker takes the next session in the rotation, moves it to the back, claims **one**
  pending resume of it and analyzes it. Sessions therefore take turns resume by resume
  (round-robin); a session that joins later gets every other turn at once.
- A session leaves the rotation when it has no pending resumes. It counts as running
  (`jobs.is_running`) until its last in-flight resume is finished; then its usage line is
  logged (F7c), once per batch.
- A resume queued while its session is leaving the rotation (upload, "retry", new
  vacancy) puts the session back; nothing is lost (existing race tests keep passing).
- Worker threads exist only while there is work; they stop when the rotation is empty.
- `SYNC_JOBS` (tests): the same rotation runs inline in the request until idle, with one
  worker, so ordering is deterministic.

## Time remaining (ETA)
- `resumes.started_at` is set when a resume is claimed (column added by migration);
  `analyzed_at` already exists. The average duration of the last 20 finished resumes
  across all sessions (the API speed is shared) is the time per resume.
- Round-robin means a session finishes after its own remaining resumes **plus** at most
  as many of every other active session's resumes. Estimate:
  `seconds = avg × Σ_over_active_sessions min(remaining_s, remaining_this) / workers`,
  where remaining = pending + processing.
- Shown on the session page under the progress while the analysis runs, refreshed with
  the status poll: "≈ 3 min left", "less than a minute left", or "estimating…" before the
  first resume is done. RU and KK. Rounded up to whole minutes.
- `/sessions/<id>/status` returns `eta_seconds` (null when unknown or idle).

## Acceptance criteria
- With two sessions queued, resumes are analyzed alternately A, B, A, B, … (test).
- Existing job, retry and race tests still pass.
- ETA formula covered by unit tests; the page shows it in RU and KK.
