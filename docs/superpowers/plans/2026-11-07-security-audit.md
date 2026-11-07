# Security Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver persistent, access-controlled submission auditing with a working Dashboard panel and regression evidence.
**Architecture:** Scoped FastAPI route wrapper for denied submissions; transactional scheduler hook for accepted HTTP submissions; independent audit table/API/UI.
**Tech Stack:** Existing FastAPI, SQLite, pytest, vanilla JS/CSS and Playwright.
**Spec:** docs/superpowers/specs/2026-11-07-security-audit-design.md

## Global Constraints
- User's approved scope is audit only. No requester authentication, new quota policies, GPU integration or planner redesign.
- Preserve existing public request/response bodies and worker behavior. Add X-Request-ID on job submission responses.
- Keep production .env, database, tunnel and Windows processes untouched; no merge or deployment.
- Latest main baseline d4a5051; branch feature/security-audit; existing cloud checkout is isolated from the field deployment.
- Record only fixed redacted details. Reader credential is server-configured, held only in current-page memory, never transmitted to unrelated endpoints.

## Review Focus
- Framework-level malformed JSON, unknown fields and unknown types must produce audited denials without echoing payload into the audit store (Task 1).
- Audit storage failure cannot leave accepted orphan work or report a false successful creation (Task 1).
- Forged request/actor headers and worker tokens must not grant reader access or trusted identity (Task 1).
- Counters must represent the persisted lifetime set, not just the latest page, and survive repeated migrations/restarts (Task 1).
- Late UI responses after locking, unavailable APIs, rejected credentials and mock mode must not expose stale audit data or fabricated counts (Task 2).

### Task 1: Persistent audit and protected API
**Files:** backend/security_audit.py (new), backend/db.py, backend/main.py, backend/scheduler.py, tests/test_security_audit.py (new).
**Interfaces:** record(conn, request_id, decision, reason_code, now, job_id=None); record_denied(request_id, reason_code); events(limit); AuditedJobRoute; scheduler.create_job(params, now, audit_request_id=None). Response: {generated_at, scope:'since_audit_enabled', totals:{allowed,blocked}, events:[{event_id,timestamp,request_id,actor_id,action,decision,reason_code,details,job_id}]}.
- [ ] Write HTTP/database tests for reader auth, exact redacted success/failure events, unique IDs, malformed JSON, quota rejection, restart/counts, atomic rollback and unchanged worker completion.
- [ ] Run tests against baseline and observe missing-feature failures.
- [ ] Implement schema, scoped route wrapper, transactional recording and protected no-store endpoint.
- [ ] Run security tests and full provider-free pytest suite; commit backend implementation.

### Task 2: Dashboard panel and browser verification
**Files:** backend/static/security-audit.js (new), index.html, style.css; scripts/check_security_audit_ui.js (new browser acceptance script).
**Interfaces:** protected API from Task 1, dedicated fetch Authorization header; no TG.api credential changes. UI IDs prefixed audit-.
- [ ] Write browser scenarios for initial lock, real events/counters, credential rejection, lock/stale responses, error/retry, mock and mobile; observe baseline failure.
- [ ] Implement password-input unlock, in-memory credential, five-second polling, safe event rendering and independent error state.
- [ ] Run browser scenarios and capture desktop/mobile evidence; commit frontend.

### Task 3: Handoff, integration and review
**Files:** CONTRACT.md, README.md, DEMO.md; scripts/security_demo.py (new).
- [ ] Add disposable-server demo that checks accepted/rejected requests, redaction and no jobs/tasks from denied requests.
- [ ] Document token setup, audit-only scope, lifetime counts, storage-failure behavior, deployment and tests; preserve latest field measurements.
- [ ] Run demo + existing 13-stage smoke on fresh loopback databases; actual browser worker computes accepted task; run Python/Node/whitespace checks.
- [ ] Request fresh whole-branch review, fix important findings with regression tests, commit and create PR. Do not merge or deploy.
