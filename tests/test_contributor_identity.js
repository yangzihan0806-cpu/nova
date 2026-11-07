// Run with: node --test tests/test_contributor_identity.js
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../backend/static/contributor.js"), "utf8");
const generatedPattern = /^dev_[0-9a-f]{16}$/;

function storage(initial = {}) {
    const data = new Map(Object.entries(initial));
    return {
        getItem(key) { return data.get(key) ?? null; },
        setItem(key, value) { data.set(key, String(value)); },
    };
}

function browser(local = storage(), session = storage()) {
    const requests = [];
    const workers = [];
    const context = vm.createContext({
        window: {}, navigator: { userAgent: "Identity regression test" },
        crypto: webcrypto, localStorage: local, sessionStorage: session, AbortSignal,
        setTimeout, clearTimeout,
        setInterval() { return 1; }, clearInterval() {},
        Worker: class {
            constructor() { this.messages = []; workers.push(this); }
            postMessage(message) { this.messages.push(message); }
            terminate() {}
        },
        async fetch(url, options) {
            const body = JSON.parse(options.body);
            requests.push({ url, body });
            return { ok: true, json: async () => ({
                worker_id: `wkr_${requests.length}`, token: "token",
                heartbeat_interval_s: 5,
            }) };
        },
    });
    vm.runInContext(source, context);
    return { Contributor: context.window.TartanContributor, context, requests, workers };
}

test("refresh and re-registration keep one device ID while worker IDs change", async () => {
    const local = storage();
    const first = browser(local);
    const worker = new first.Contributor({ name: "Browser", slots: 1 });
    await worker.register();
    await worker.register();
    const refreshed = browser(local);
    await new refreshed.Contributor({ name: "Browser", slots: 1 }).register();
    const identity = first.requests[0].body.device_id;
    assert.match(identity, generatedPattern);
    assert.equal(first.requests[1].body.device_id, identity);
    assert.equal(refreshed.requests[0].body.device_id, identity);
    assert.equal(local.getItem("tg_device_id"), identity);
});

test("existing valid ID is retained without rewriting storage", async () => {
    const local = storage({ tg_device_id: "dev_existing123" });
    let writes = 0;
    local.setItem = () => { writes++; };
    const page = browser(local);
    await new page.Contributor({ name: "Browser", slots: 1 }).register();
    assert.equal(page.requests[0].body.device_id, "dev_existing123");
    assert.equal(writes, 0, "must not rewrite an existing valid identity");
});

test("invalid stored ID is replaced with cryptographic random lowercase hex", async () => {
    const local = storage({ tg_device_id: "invalid-device" });
    const page = browser(local);
    page.context.crypto = { getRandomValues(bytes) {
        assert.equal(bytes.length, 8);
        bytes.set([0, 1, 2, 3, 171, 205, 239, 255]);
        return bytes;
    } };
    await new page.Contributor({ name: "Browser", slots: 1 }).register();
    assert.equal(page.requests[0].body.device_id, "dev_00010203abcdefff");
    assert.equal(local.getItem("tg_device_id"), "dev_00010203abcdefff");
});

test("localStorage denial falls back to sessionStorage across reloads", async () => {
    const session = storage();
    const first = browser(undefined, session);
    Object.defineProperty(first.context, "localStorage", { get() { throw new Error("SecurityError"); } });
    await new first.Contributor({ name: "Browser", slots: 1 }).register();
    const next = browser(undefined, session);
    Object.defineProperty(next.context, "localStorage", { get() { throw new Error("SecurityError"); } });
    await new next.Contributor({ name: "Browser", slots: 1 }).register();
    const identity = first.requests[0].body.device_id;
    assert.match(identity, generatedPattern);
    assert.equal(next.requests[0].body.device_id, identity);
});

test("storage quota failures keep one identity for all contributors in this page", async () => {
    const blocked = { getItem() { return null; }, setItem() { throw new Error("QuotaExceededError"); } };
    const page = browser(blocked, blocked);
    const worker = new page.Contributor({ name: "Browser", slots: 1 });
    await worker.register();
    await worker.register();
    await new page.Contributor({ name: "Another", slots: 2 }).register();
    assert.match(page.requests[0].body.device_id, generatedPattern);
    assert.equal(new Set(page.requests.map(({ body }) => body.device_id)).size, 1);
});

test("both storage getters can fail without stopping registration", async () => {
    const page = browser();
    for (const name of ["localStorage", "sessionStorage"]) {
        Object.defineProperty(page.context, name, { get() { throw new Error("SecurityError"); } });
    }
    await new page.Contributor({ name: "Browser", slots: 1 }).register();
    assert.match(page.requests[0].body.device_id, generatedPattern);
});

test("owner is optional, trims whitespace, and does not change the device identity", async () => {
    const page = browser();
    for (const owner of ["  hyang2  ", "   ", undefined]) {
        await new page.Contributor({ name: "Browser", slots: 1, owner }).register();
    }
    assert.equal(page.requests[0].body.owner, "hyang2");
    assert.equal(page.requests[1].body.owner ?? null, null);
    assert.equal(page.requests[2].body.owner ?? null, null);
    assert.equal(new Set(page.requests.map(({ body }) => body.device_id)).size, 1);
});

test("multiple slots still share one registration with the persistent device ID", async () => {
    const page = browser();
    const contributor = new page.Contributor({ name: "Browser", slots: 3, owner: "hyang2" });
    await contributor.start();
    assert.equal(page.requests.length, 1);
    assert.equal(page.requests[0].body.slots, 3);
    assert.equal(page.requests[0].body.owner, "hyang2");
    assert.match(page.requests[0].body.device_id, generatedPattern);
    assert.equal(page.workers.length, 3);
    assert.deepEqual(page.workers.map(worker => worker.messages[0].workerId), ["wkr_1", "wkr_1", "wkr_1"]);
    contributor.clearWorkers();
});
