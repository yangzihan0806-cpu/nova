# TartanGrid · Live demo script (about 3 minutes)

Every number comes from [Measured results](README.md#measured-results) in the README. On stage we only report results that actually happened, never preset or estimated numbers.

## Devices and roles

| Device | What runs on it | Who operates it |
|---|---|---|
| Server laptop | `tg_server.bat` + `tg_tunnel.bat` + `tg_native.bat` (native, 4 slots) | Harry |
| Projector computer (**does not contribute compute**) | Dashboard / New Job / Job page | Operator next to the presenter |
| Teammate device 1 | Browser on `/contribute.html`, Owner filled in, kept in the foreground | Teammate |
| Teammate device 2 | Browser on `/contribute.html`, Owner filled in, kept in the foreground; **this one demonstrates the disconnect** | Teammate |

Rules:
- Submit jobs and watch the image on the projector computer. In the browser running the contribute page, **don't switch tabs**: switching away pauses it automatically.
- Keep contributing devices plugged in, with the screen unlocked.
- Don't minimize the native window.

## Checks 5 minutes before going on

- [ ] The server, tunnel and native windows are all open; the Dashboard shows **2 BROWSER + 1 NATIVE**, all online.
- [ ] Run one **Quick** job as a warm-up and confirm the image renders.
- [ ] No other job is running (Active jobs on the Dashboard is 0).
- [ ] The backup video plays offline on the projector computer.
- [ ] No `.env`, key or terminal token is visible on screen.

## 0:00–0:20 · The problem

Presenter:
> "Most campus computers sit idle most of the time. With TartanGrid, anyone can open a web page to contribute their idle CPU to one big job that can be split into pieces, and the job still finishes when a device leaves partway through."

Screen: Dashboard.

## 0:20–0:45 · Contributing compute

Screen: the node table on the Dashboard. Point out the two BROWSER nodes and the NATIVE node.

> "These two are browsers on teammates' devices. They opened a web page and clicked Start; nothing to install. This one runs the Python native worker, using numba on 4 cores. They all lease work through the same protocol, and a contributor can Pause or leave at any time."

## 0:45–1:15 · AI planning and submitting a job

Screen: New Job. In the AI Planner, type:

> Render a detailed Mandelbrot image and keep browser workers light.

Click Generate config. Once the form is filled in:

> "The AI only translates a sentence into parameters. It never generates code and never submits on its own, and every parameter is validated by the server."

Then **choose the Demo preset** (AI-generated parameters may take a different amount of time; the Demo timing has been measured), click Submit, go to the Job page, and **immediately tick Color by worker**.

If the AI is unavailable (503): just say "when the AI is unavailable, the form still works", choose Demo and submit.

## 1:15–1:50 · Recovering from a disconnect (the core)

About **10 seconds** after submitting (roughly 1/3 done), signal teammate device 2 to **close the contribute tab outright**.

> "Now we shut one device off. The tile it was computing isn't lost: every tile has a 10-second lease, and if it isn't returned before the lease expires, the server hands it to another device. Late or duplicate results are rejected, so nothing is counted twice."

Screen: in the event stream on the Dashboard or Job page, `lease_expired` → an orange **REASSIGNED** appears; on the Job page that tile switches to another device's color with an orange dashed outline. Wait until it appears before you describe it; don't get ahead of it.

## 1:50–2:20 · Results and real performance

Screen: the finished image and Workers on this job.

> "The whole image is 192 tiles with zero failures. We measured it: three devices together take 25.8 seconds. The same job on one browser thread takes 97.2 seconds, so that's 3.76× faster. Even against the server laptop alone on 4 cores at 43.2 seconds, it's 1.67× faster. These are all end-to-end times, including network and scheduling."

If this run's time is different, read the number shown on screen and say that the numbers above are from earlier measured runs.

> "The native worker took the most tiles because it's the fastest. That's the benefit of pull-based scheduling: faster devices automatically do more, with no manual assignment."

## 2:20–2:40 · Contribution records

Screen: Contributions page.

> "We record how much verified computation each device did, in millions of iterations. Only results that pass the server's spot checks earn credit, and computing slowly doesn't earn more. It exports to CSV so we can use it to distribute rewards later. It's credit only; no money is involved."

## 2:40–3:00 · Safety and vision

> "Only reviewed templates run, so nobody can push code onto someone else's device. Today we're showing fractal rendering; the same scheduling, fault tolerance and verification can host other splittable workloads, like Monte Carlo simulations and parameter sweeps. In the future, with administrator approval, it could also reach lab clusters through Slurm."

## When things go wrong

| Situation | What to do |
|---|---|
| The network or tunnel drops | Play the backup video and say it is an earlier recording |
| The AI returns 503 | Use the form, choose Demo, and say "without the AI, the flow works the same" |
| No REASSIGNED appears after closing the tab | Wait for the lease to expire (10 seconds); if the job has already finished, describe the measured record from run 4 (31.8 seconds, 1 tile reassigned, 0 failures) |
| The browser nodes get almost no tiles | Check that the contribute pages are in the foreground; say honestly that native is faster |
| The time on screen differs from the README | Read the live number; don't change how it is reported |

## Common judge questions

**How is this different from BOINC?** We didn't invent volunteer computing. We built a low-barrier campus version: you join by opening a browser, browsers and native workers share one protocol, dropped nodes are recovered automatically, and contributions are recorded by verified work.

**Can it use the university's GPUs?** Not now, and it isn't connected. In the future it would only connect with the resource owner's permission, through the existing scheduler and quotas.

**How do you prevent cheating?** For every result the server randomly recomputes 8 pixels; if they don't match, the result is rejected and recomputed. It's a probabilistic check, not an absolute guarantee; redundant computation could be added later.

**Can it only compute fractals?** Right now only this one reviewed template is enabled, for safety (no arbitrary code) and because it's visual. The platform layer is general; adding a new workload only requires plugging in one reviewed compute module.

**How much speedup?** 25.8 seconds on 3 devices: 3.76× faster than one browser thread and 1.67× faster than the server laptop alone, all measured end to end. Two early runs were slower than a single machine because of a localhost configuration issue on Windows; the README records them as they happened.
