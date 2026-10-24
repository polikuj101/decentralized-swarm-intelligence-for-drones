# TODOS

## 1. Live verification of Section 9 (investor demo)
- **What:** One honest live run: 5 different orders compile to an identical `manifest.json` on all 5 drones, p95 compile latency <= 4 s, Acts 2-3 of `scripts/run_demo.sh` complete, BDA cards appear in the gallery with the correct image.
- **Why:** Section 9 (commit d78bcd9) was written without a full live run (risk 9.8 in the spec); only one manifest compilation is evidenced. Code was produced by Gemini from a Claude-written spec, so treat the implementation report as unverified.
- **Pros:** know what actually works before any showing. **Cons:** stand time.
- **Context:** `docker/drone-agent/swarm/tasking.py`, `evidence.py`, `scripts/run_demo.sh`, `scenarios/05_investor_demo.yaml` (trigger times are marked "starting approximation").
- **Depends on:** running stand (`make up`).

## 2. Fail-closed EW_TOKEN
- **What:** With an empty `EW_TOKEN`, HUD, tasking, evidence and ew_agent must refuse mutating requests instead of skipping the check.
- **Why:** an unset token silently disables all authorization, including EW control.
- **Pros:** closes the root cause. **Cons:** breaks token-less local runs; set the token in `docker-compose*.yml` and `scripts/run_demo.sh`.
- **Context:** `docker/web-hud/main.py:159`, `:282`; `docker/drone-agent/swarm/tasking.py:114`; `docker/drone-agent/ew/ew_agent.py:288`.
- **Depends on:** the `/api/tasking` guard fix from the 2026-10-24 eng review (warning only on empty token).

## 3. Debounce epoch bumps on alive-set change
- **What:** 300-500 ms window on `alive_set` changes so peers rejoining a partition cause one epoch bump instead of 15-17 per split/merge cycle.
- **Why:** each bump resets the CBAA auction; a skeptical engineer will ask "why 15 epochs in a 6 s merge?"; this is the demoed phase.
- **Pros:** cleaner epoch trace, less churn. **Cons:** touches consensus code; chaos tests must be re-run.
- **Context:** `docker/drone-agent/swarm/coordinator.py` around the `alive != self.alive_prev` / `self.bump("alive-set", now)` branch. Metric: epochs per cycle.
- **Depends on:** none; re-run `scripts/stress_test.py` and `verify_ground_truth.py` after.
