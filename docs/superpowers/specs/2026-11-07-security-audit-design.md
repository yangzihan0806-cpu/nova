# Submission security audit

The user approved a one-hour target for a complete, narrowly scoped audit feature after reviewing the Sandia requirements. This implements the audit slice, not the full authorization/quotas PRD.

Keep current anonymous job submission, validation, global concurrency limits, worker protocols, contribution accounting and planner behavior. Add a SQLite audit table, a protected read API and a Dashboard card. Do not operate the live Windows server, its tunnel or database. Use the current cloud checkout on a feature branch and temporary databases for tests.

Approach: persist audit rows in the existing SQLite database, rather than an in-memory ring (would lose evidence on restart) or the public operational event feed (would expose audit details). Successful HTTP job creation writes one allowed event in the same transaction as the job/tasks. A scoped route wrapper records validation failures and existing 4xx rejections. Only fixed reason/details strings are stored; no bodies, prompts, headers, IPs, names or tokens. All actors are explicitly anonymous because requester identity is not verified. Server-generated request IDs are returned in X-Request-ID; client IDs are ignored.

GET /api/security/events?limit=50 returns newest events, lifetime allowed/blocked counts and server time. Limit is 1–100. It requires a dedicated Bearer credential from TG_SECURITY_AUDIT_TOKEN (32–256 URL-safe characters); missing/invalid configuration disables reading with 503. Missing/wrong credentials return 401. Responses prohibit caching. Credential access is independent of worker tokens; no credential is embedded in code, URLs or browser storage.

The Dashboard card starts locked, asks for the read credential, polls every five seconds and can be locked again. It clears sensitive state on lock/navigation. Failure clears counts to unavailable, never invented zero. Mock mode explicitly has no security evidence. All text uses DOM text nodes. Existing panels keep working independently.

If successful audit persistence fails, job creation rolls back and returns 503. Rejected submissions remain non-runnable; an audit write failure is reported as 503. These storage-failure behaviors are documented rather than silently claiming complete evidence. Audit persistence is not tamper-proof or a substitute for request authentication.

Acceptance: a valid job completes through existing workers; invalid/malformed/over-limit submissions produce exactly one redacted denied event and no new work; records survive restart; unauthorized readers cannot see events; UI unlock, refresh, lock, unavailable and mobile behavior pass; existing Python, Node and HTTP smoke suites stay green.
