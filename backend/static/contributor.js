class TartanContributor {
    constructor({ name, slots }) {
        this.name = name;
        this.slots = slots;
        this.state = "idle";
        this.registration = null;
        this.workers = [];
        this.listeners = Object.create(null);
        this.tasksDone = 0;
        this.lastRuntimeMs = null;
        this.lastError = null;
        this.heartbeatTimer = null;
        this.recovered = false;
        this.recoveryPending = false;
        this.queue = Promise.resolve();
    }

    on(type, fn) {
        (this.listeners[type] ??= []).push(fn);
    }

    emit(type, value) {
        for (const fn of this.listeners[type] ?? []) fn(value);
    }

    status() {
        this.emit("status", {
            state: this.state,
            workerId: this.registration?.worker_id ?? null,
            slots: this.slots,
            tasksDone: this.tasksDone,
            currentTaskIds: this.workers.flatMap(slot => slot.taskId == null ? [] : [slot.taskId]),
            lastRuntimeMs: this.lastRuntimeMs,
            lastError: this.lastError,
        });
    }

    logError(message) {
        this.lastError = message;
        this.emit("log", message);
        this.status();
    }

    enqueue(action) {
        const next = this.queue.then(action);
        this.queue = next.catch(() => {});
        return next;
    }

    async post(path, body, registration, attempts = 3) {
        let delay = 500;
        for (let attempt = 0; attempt < attempts; attempt++) {
            try {
                const headers = { "Content-Type": "application/json" };
                if (registration) headers.Authorization = `Bearer ${registration.token}`;
                const response = await fetch(path, {
                    method: "POST", headers, body: JSON.stringify(body),
                    signal: AbortSignal.timeout(3000), redirect: "error",
                });
                if (response.ok) return await response.json();
                const failure = new Error(`${path} failed (HTTP ${response.status})`);
                failure.status = response.status;
                throw failure;
            } catch (err) {
                const retryable = !err.status || [408, 429].includes(err.status) || err.status >= 500;
                if (!retryable || attempt + 1 === attempts) throw err;
                await new Promise(resolve => setTimeout(resolve, delay));
                delay = Math.min(8000, delay * 2);
            }
        }
    }

    async register() {
        const registration = await this.post("/api/workers/register", {
            name: this.name,
            worker_type: "browser",
            slots: this.slots,
            has_gpu: false,
            task_types: ["mandelbrot_v1"],
            client_info: Array.from(navigator.userAgent).slice(0, 200).join(""),
        });
        if (typeof registration.worker_id !== "string" || typeof registration.token !== "string" ||
            !Number.isFinite(registration.heartbeat_interval_s) || registration.heartbeat_interval_s <= 0) {
            throw new Error("Invalid registration response");
        }
        this.registration = registration;
    }

    workerRequest(action, registration = this.registration, attempts) {
        return this.post(`/api/workers/${registration.worker_id}/${action}`, {}, registration, attempts);
    }

    beginHeartbeat() {
        const registration = this.registration;
        let pending = false;
        // Hidden tabs can throttle this main-thread timer. The page owns visibility-based pause.
        this.heartbeatTimer = setInterval(async () => {
            if (pending || this.registration !== registration) return;
            pending = true;
            try {
                await this.workerRequest("heartbeat", registration);
                if (this.registration === registration && !this.recoveryPending) this.recovered = false;
            } catch (err) {
                if (this.registration !== registration) return;
                this.logError(err.message);
                if (err.status === 401) this.recover(registration);
            } finally {
                pending = false;
            }
        }, registration.heartbeat_interval_s * 1000);
    }

    beginWorkers() {
        const { worker_id: workerId, token } = this.registration;
        const registration = this.registration;
        for (let slot = 0; slot < this.slots; slot++) {
            const worker = new Worker("/worker.js");
            const entry = { worker, taskId: null };
            this.workers.push(entry);
            worker.onmessage = ({ data }) => {
                if (this.registration !== registration || !this.workers.includes(entry)) return;
                if (data.type === "state") {
                    entry.taskId = data.state === "working" ? data.taskId : null;
                    this.status();
                } else if (data.type === "task_done") {
                    const { taskId, jobId, runtimeMs } = data;
                    entry.taskId = null;
                    this.tasksDone++;
                    this.lastRuntimeMs = runtimeMs;
                    this.emit("task_done", { taskId, jobId, runtimeMs });
                    this.status();
                } else if (data.type === "error") {
                    this.logError(data.message);
                    if (data.status === 401) this.recover(registration);
                } else if (data.type === "log") {
                    this.emit("log", data.message);
                }
            };
            worker.onerror = () => {
                entry.taskId = null;
                this.logError(`Worker slot ${slot + 1} failed`);
            };
            if (this.state === "paused") worker.postMessage({ cmd: "pause" });
            worker.postMessage({ cmd: "start", workerId, token, slot });
        }
    }

    clearWorkers() {
        clearInterval(this.heartbeatTimer);
        this.heartbeatTimer = null;
        const workers = this.workers;
        this.workers = [];
        return workers;
    }

    async disconnect(registration) {
        try {
            await this.workerRequest("disconnect", registration, 2);
        } catch (err) {
            this.logError(err.message);
        }
    }

    async connect(paused = false) {
        this.state = "connecting";
        this.status();
        try {
            await this.register();
            if (paused) await this.workerRequest("pause");
            this.state = paused ? "paused" : "running";
            this.beginHeartbeat();
            this.beginWorkers();
            this.lastError = null;
            this.status();
        } catch (err) {
            for (const { worker } of this.clearWorkers()) worker.terminate();
            const registration = this.registration;
            this.registration = null;
            if (registration) await this.disconnect(registration);
            this.state = "error";
            this.logError(err.message);
            throw err;
        }
    }

    recover(registration) {
        if (this.recoveryPending) return;
        this.recoveryPending = true;
        this.enqueue(async () => {
            try {
                if (this.registration !== registration) return;
                const paused = this.state === "paused";
                for (const { worker } of this.clearWorkers()) worker.terminate();
                this.registration = null;
                if (this.recovered) {
                    this.state = "error";
                    this.logError("Worker authentication failed after re-registration");
                    return;
                }
                this.recovered = true;
                await this.connect(paused);
            } finally {
                this.recoveryPending = false;
            }
        }).catch(() => {}); // connect already reported the error.
    }

    start() {
        return this.enqueue(async () => {
            if (this.registration) return;
            this.tasksDone = 0;
            this.lastRuntimeMs = null;
            this.lastError = null;
            this.recovered = false;
            await this.connect();
        });
    }

    setPaused(paused) {
        return this.enqueue(async () => {
            if (this.state !== (paused ? "running" : "paused")) return;
            const action = paused ? "pause" : "resume";
            try {
                await this.workerRequest(action);
            } catch (err) {
                this.logError(err.message);
                if (err.status === 401) this.recover(this.registration);
                throw err;
            }
            this.state = paused ? "paused" : "running";
            for (const { worker } of this.workers) worker.postMessage({ cmd: action });
            this.status();
        });
    }

    pause() { return this.setPaused(true); }
    resume() { return this.setPaused(false); }

    stop() {
        return this.enqueue(async () => {
            const workers = this.clearWorkers();
            const registration = this.registration;
            this.registration = null;
            for (const { worker } of workers) worker.postMessage({ cmd: "stop" });
            const disconnected = registration ? this.disconnect(registration) : Promise.resolve();
            if (workers.length) {
                await new Promise(resolve => setTimeout(resolve, 500));
                for (const { worker } of workers) worker.terminate();
            }
            await disconnected;
            this.state = "stopped";
            this.status();
        });
    }
}

if (typeof window !== "undefined") window.TartanContributor = TartanContributor;
