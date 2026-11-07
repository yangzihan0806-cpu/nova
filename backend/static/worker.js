importScripts("/mandelbrot_kernel.js");

let paused = false;
let stopped = false;
let started = false;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const state = (value, taskId) => postMessage({ type: "state", state: value, taskId });
const error = (message, status) => postMessage({ type: "error", message, status });

async function post(path, token, body, leasing = false) {
    let delay = 500;
    while (!stopped && (!leasing || !paused)) {
        try {
            const response = await fetch(path, {
                method: "POST",
                headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
                body: JSON.stringify(body),
                signal: AbortSignal.timeout(3000),
                redirect: "error",
            });
            if (![408, 429].includes(response.status) && response.status < 500) return response;
            error(`Request failed (HTTP ${response.status}); retrying`, response.status);
        } catch {
            error("Network request failed; retrying");
        }
        if (stopped || (leasing && paused)) return null;
        await sleep(delay);
        delay = Math.min(8000, delay * 2);
    }
    return null;
}

async function run(workerId, token) {
    let idleStreak = 0;
    try {
        while (!stopped) {
            if (paused) {
                state("paused");
                await sleep(500);
                continue;
            }
            state("idle");
            const lease = await post(`/api/workers/${workerId}/lease`, token, {}, true);
            if (stopped) break;
            if (!lease) continue;
            if (lease.status === 401) {
                error("Worker authentication expired", 401);
                break;
            }
            if (lease.status === 204) {
                await sleep(Math.min(3000, 500 + Math.random() * 500 + idleStreak * 250));
                idleStreak = Math.min(10, idleStreak + 1);
                continue;
            }
            if (lease.status !== 200) {
                error(`Lease failed (HTTP ${lease.status})`, lease.status);
                await sleep(500);
                continue;
            }
            idleStreak = 0;
            let task;
            try {
                task = await lease.json();
                if (!Number.isInteger(task.task_id) || typeof task.lease_token !== "string") {
                    throw new Error("Invalid lease");
                }
            } catch {
                error("Invalid lease response");
                await sleep(500);
                continue;
            }
            if (stopped) break;
            const { task_id: taskId, job_id: jobId, task_type: taskType, lease_token: leaseToken } = task;
            state("working", taskId);
            const body = { worker_id: workerId, lease_token: leaseToken };
            let action = "complete";
            const start = performance.now();
            try {
                if (taskType !== "mandelbrot_v1") throw new Error("Unsupported task type");
                body.result = Array.from(computeTile(task.input));
                body.runtime_ms = Math.round(performance.now() - start);
            } catch (err) {
                action = "fail";
                body.error = String(err).slice(0, 500);
                error(`Task ${taskId} failed: ${body.error}`);
            }
            const report = await post(`/api/tasks/${taskId}/${action}`, token, body);
            if (!report) continue;
            if (report.status === 401) {
                error("Worker authentication expired", 401);
                break;
            }
            if (report.status === 409) {
                postMessage({ type: "log", message: `Task ${taskId}: stale lease, dropped` });
            } else if (report.status !== 200) {
                error(`Task ${taskId} ${action} failed (HTTP ${report.status})`, report.status);
            } else if (action === "complete") {
                postMessage({ type: "task_done", taskId, jobId, runtimeMs: body.runtime_ms });
            }
        }
    } finally {
        state("stopped");
    }
}

self.onmessage = ({ data }) => {
    switch (data.cmd) {
        case "start":
            if (!started && !stopped) {
                started = true;
                run(data.workerId, data.token).catch(err => error(String(err)));
            }
            break;
        case "pause":
            paused = true;
            break;
        case "resume":
            paused = false;
            break;
        case "stop":
            stopped = true;
            if (!started) state("stopped");
            break;
    }
};
