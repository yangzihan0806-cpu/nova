/* Independent, read-only audit panel. Never add its credential to shared API calls. */
(function () {
  "use strict";
  const $ = TG.$, el = TG.el;
  let credential = "", generation = 0, timer = null, controller = null;

  function clearEvidence() {
    $("#audit-allowed").textContent = $("#audit-blocked").textContent = "—";
    $("#audit-events").replaceChildren();
    $("#audit-scope").hidden = true;
  }

  function lock(message = "Locked — enter the audit reader credential to view events.") {
    generation++;
    credential = "";
    clearTimeout(timer);
    if (controller) controller.abort();
    controller = null;
    $("#audit-token").value = "";
    $("#audit-form").hidden = false;
    $("#audit-lock").disabled = true;
    $("#audit-status").textContent = message;
    clearEvidence();
  }

  function render(data) {
    if (!data || !Array.isArray(data.events) || !data.totals ||
        ![data.totals.allowed, data.totals.blocked].every(n => Number.isSafeInteger(n) && n >= 0)) {
      throw new Error("Invalid audit response");
    }
    const rows = data.events.map(event => el("li", null,
      el("div", {class: "audit-event-head"},
        el("strong", {class: event.decision === "allowed" ? "audit-allow" : "audit-deny"},
          event.decision === "allowed" ? "Allowed" : "Blocked"),
        el("time", {class: "muted small", datetime: event.timestamp}, TG.fmtTime(event.timestamp))),
      el("div", {class: "mono small audit-id"}, event.reason_code),
      el("div", {class: "small"}, event.details),
      el("div", {class: "muted small mono audit-id"}, event.request_id),
      event.job_id ? el("a", {class: "mono small", href: "/job.html?id=" + encodeURIComponent(event.job_id)}, event.job_id) : null));
    $("#audit-events").replaceChildren(...rows);
    $("#audit-allowed").textContent = TG.fmtInt(data.totals.allowed);
    $("#audit-blocked").textContent = TG.fmtInt(data.totals.blocked);
    $("#audit-scope").hidden = false;
    $("#audit-status").textContent = rows.length ? "Updated " + TG.fmtTime(data.generated_at) : "No submissions recorded since auditing was enabled.";
  }

  async function poll(epoch) {
    const active = new AbortController();
    controller = active;
    const timeout = setTimeout(() => active.abort(), 10000);
    try {
      const response = await fetch("/api/security/events?limit=50", {
        headers: {Authorization: "Bearer " + credential},
        cache: "no-store", credentials: "omit", redirect: "error", signal: active.signal,
      });
      if (epoch !== generation) return;
      if (response.status === 401 || response.status === 403) {
        lock("Access denied — check the audit reader credential.");
        return;
      }
      if (!response.ok) throw new Error("Audit unavailable");
      const data = await response.json();
      if (epoch !== generation) return;
      render(data);
    } catch (_) {
      if (epoch !== generation) return;
      clearEvidence();
      $("#audit-status").textContent = "Security events unavailable — retrying. The server operator can check audit configuration and storage.";
    } finally {
      clearTimeout(timeout);
      if (controller === active) controller = null;
      if (epoch === generation && credential) timer = setTimeout(() => poll(epoch), 5000);
    }
  }

  $("#audit-form").addEventListener("submit", event => {
    event.preventDefault();
    if (TG.MOCK) return;
    const entered = $("#audit-token").value.trim();
    lock();
    if (!entered) return;
    credential = entered;
    $("#audit-form").hidden = true;
    $("#audit-lock").disabled = false;
    $("#audit-status").textContent = "Loading security events…";
    poll(generation);
  });
  $("#audit-lock").addEventListener("click", () => lock());
  window.addEventListener("pagehide", () => lock());
  if (TG.MOCK) {
    $("#audit-token").disabled = $("#audit-unlock").disabled = true;
    $("#audit-status").textContent = "Live backend required. Mock data is not security evidence.";
  }
})();
