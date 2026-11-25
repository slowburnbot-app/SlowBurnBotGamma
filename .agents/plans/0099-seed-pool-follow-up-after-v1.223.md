---
id: 99
title: Seed pool follow-up after v1.223
task: 0099-seed-pool-follow-up-after-v1.223
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-11-25
updated: 2026-11-25
---

## Goal

Check that the seed pool of bot v1.223 works in real runs. Then make the rate rule stateless and give the numbers a date window.

## Context

- Bot v1.223 and the backend were released on 2026-11-25. All 4 clients run v1.223 (checked 2026-11-25). No account had a run left that day, so no run on v1.223 is checked yet.
- Done in v1.222 and v1.223: see item #12. The last rule: a seed rests 30 days when the Wilson 95% upper bound of its follow-back rate in the running action is under half of the pool average of that action. The pick weight is `(fb + 10*base) / (complete + 10)`.
- Findings of the second Fable review that are still open:
  - A rested seed returns after 30 days with the same all-time numbers and can rest again on its first use (a flap loop). In a simulation a seed with a true 5% rate still got 304 follows in a year.
  - The numbers of 1 action can rest a seed for all actions (one `active` flag).
  - `by_action` and `action_rates` have no date window. A new seed has only recent data, the baseline is all-time, so a fall in follow-back can rest the new seeds.
  - The baseline includes the seed's own data (lenient, safe). `MIN_POOL_RATE_SAMPLE` is 30; Fable suggests 100.
  - The floor of 0.01 weakens the saturation damping when the pool average is very low.
- `two_robbers` was retired for good by the old rule. The user turns it on again on the dashboard (account `timeforashifty`).
- From the work of this session: the same 2 patterns came back many times: a CRLF-safe file edit (a scratch helper, `crlfedit.py`) and a read-only production query (`railway run --service SlowBurnBotGamma -- python3 <script>`).

## Decisions

- 2026-11-25: the stateless rule and the window wait for a few days of real runs (the user and I agreed). They do not block v1.223.

## Dead ends

None.

## Plan

1. [ ] Pull the run logs of `timeforashifty` (`scripts/fetch_run_logs.py`) after its first run on v1.223. Check: no "no posts found" lines, no crash in the account-list actions (the call order of `action_type` changed in `bot-client/burnBot_followGroup.py`), any "retiring seed" or "resting" line names the action, the pool stays above 10.
2. [ ] Check the log of the other 3 clients for errors (`docker logs slowburn-client0N`, strip ANSI).
3. [ ] The user turns `two_robbers` on again on the dashboard.
4. [ ] Make the rate rule stateless and per action: decide at load time in `load_seed_pool` and `decide_seed_use` (`bot-client/burnBot_seeds.py`), exclude or floor the seed for that run, write no retirement.
5. [ ] Add a window (`follow_date` in the last 120 days) to `by_action` and `action_rates` in `app/services/follow_seeds.py`. Keep the all-time numbers for the dashboard.
6. [ ] Decide on a pool average without the seed's own data, and on `MIN_POOL_RATE_SAMPLE` 100.
7. [ ] Make a helper for CRLF-safe edits and for read-only production queries (a script in `scripts/`, or a skill).

## Key files

- `bot-client/burnBot_seeds.py` — `decide_seed_use`, `seed_weight`, `load_seed_pool`, `SeedBook`.
- `app/services/follow_seeds.py` — `seed_stats_by_handle`, `pool_action_rates`, the cool-down.
- `bot-client/burnBot_followGroup.py` and `bot-client/burnBot_followEngagers.py` — the callers.
- `tests/test_seed_logic.py` and `tests/test_follow_seed_service.py`.
- Memory note `project_seed_pool_review_2026_11_25`.
