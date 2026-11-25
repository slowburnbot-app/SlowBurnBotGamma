---
id: 12
title: Liker seeds mostly exhausted
task: 0012-liker-seeds-mostly-exhausted
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-10-14
updated: 2026-11-25
---

## Goal

Stop the bot from wasting time on liker seeds that have no new people left.

## Context

- A review of the run logs from 2026-10-09 to 2026-10-14 (bot v1.210 at that time; v1.219 on
  2026-11-01) found the problem.
- 1,489 of 1,920 `likers` entries (about 78%) were skipped as "in database".
- The same usernames repeat across sessions.
- Some `timeforashifty` seeds were scanned 4 to 5 times: `steadfast_screen_printing`, `fell.1.earth`.
- The pool of new people from these seeds is mostly exhausted.
- No code change is made. This item is a finding only. No commit after
  2026-09-08 changes `bot-client/burnBot_seeds.py` or `bot-client/burnBot_followEngagers.py`.
- A read-only check on 2026-11-01 (`fetch_run_logs.py --kind run_log --hours 168`) returned 54
  run logs for 2026-10-26 to 2026-10-31. The problem persists: `steadfast_screen_printing` was
  scanned 3 times in that window, with 2,826 "[in database]" skip lines out of 3,450 skip lines.
  `fell.1.earth` was not scanned in that window. The exact 78% of the first review did not
  reproduce: the per-target saturation values in that window are mostly 0-58%, below the
  threshold of 80%.
- The retire rules that exist now (all in `finish_seed_use`, `bot-client/burnBot_seeds.py`,
  called from `bot-client/burnBot_followEngagers.py` at about line 436):
  - A saturation rule: `SATURATION_RETIRE = 80`. A seed retires after 2 consecutive uses at or
    above 80% known.
  - A rate rule: a seed retires when its follow-back percentage is low.
  - A "not found" rule.
- The gap: `finish_seed_use` does nothing for a seed with no `id`. A seed gets an `id` only when
  the `account_group_mode` of the account is `pool`. In `manual` mode (the default) the pool is
  only the textarea, so no seed is ever retired.
- Log evidence of the 168 h window: only `marketalleygin` logs "seed [..] used, saturation="
  lines (11) and "retiring seed" warnings (2, both by the follow-back rule: `mollymalloys` and
  `phillymagfood`). `timeforashifty`, the account with the repeated seeds, logged none. This
  fits `timeforashifty` being in `manual` mode. Its actual setting is unknown on 2026-11-01.
- Update 2026-11-25: `timeforashifty` runs in `pool` mode (since 2026-11-20). A review of its run logs
  and a Fable review found the real causes, and the code changed in bot v1.222 and v1.223 (all
  pushed and released):
  - 5 of 10 discovered seeds were private profiles. They never retired and discovery never ran.
    They retire now (private text at once; other "no posts" need 2 strikes).
  - The saturation metric could not see an exhausted seed: rows filtered on an earlier run counted
    only in the denominator. They now count as known. A use needs 20 rows to count when it followed someone.
    Two saturated uses of the same action start a 30-day cool-down (the server brings the seed back).
  - A run keeps bad seed outcomes back unless another seed worked in the same run (`SeedBook`).
    After 3 failures in a row with no success, the run stops.
  - Discovery reads the "See all" dialog and keeps only public accounts with at least 3 posts.
  - Typed account-group handles become `manual` seeds in pool mode. `[del]` is a tombstone.
  - `follow_targets` has a unique index on (account_id, lower(target_handle)); the dedupe removed 32 rows.
  - The rate rule judged likers seeds against the account-wide follow-back rate (35%). It now uses
    the pool average of the same action (likers 21% on 2026-11-25), the Wilson 95% upper bound under
    half of it, a 30-day cool-down, and a pool-average prior for the pick weight.
  - Tests are in `tests/`. Run: `.venv/bin/python -m unittest discover -s tests -t .`.
- Open after v1.223: the first runs on v1.223 are not checked yet (2026-11-25 had no runs left).
  `two_robbers` was retired for good by the old rule and the user must turn it on again on the
  dashboard. Follow-up work from the second Fable review: see the item "Seed rate rule: stateless
  per action" (a rested seed returns with the same history and can rest again; one action can rest a
  seed for all actions; no date window for the numbers).

## Decisions

- The user chose a retire rule for exhausted liker seeds. The other option was seed rotation.
- 2026-11-25: the user chose to fix the pool logic (strikes, cool-down, per-action baseline) and not to rotate seeds by hand.
- 2026-11-25: a rate retirement and an exhaustion retirement are cool-downs (30 days). A private profile, "not found" and a seed that the user removes stay out for good.

## Dead ends

- A fixed 0.5 prior for new seeds gave them about 80% of the picks while their real rate is 0.1 to 0.3 (replaced by the pool average).
- Comparing a likers seed with the account-wide follow-back rate (35%): 8 of 25 seeds would have retired on first use.

## Plan

1. [x] Check the `account_group_mode` of `timeforashifty`: it is `pool` (2026-11-25).
2. [ ] Proposal, for the user to decide: make the retire rule apply to accounts in `manual` mode (a manual list has no seed rows, so no seed retires there).
3. [x] Make the saturation rule see exhausted seeds: old filtered rows count as known, minimum 20 rows (v1.222). The threshold `SATURATION_RETIRE` stays 80.
4. [x] Rework the seed pool: strikes, cool-down, `SeedBook`, vetted discovery, tombstone, unique follow target (v1.222).
5. [x] Per-action baseline, Wilson bound, pool-average prior (v1.223).
6. [ ] Check the first runs on v1.223 and the logs of all 4 clients (see the item "Check first v1.223 seed runs").
7. [ ] Stateless per-action rate rule and a 120-day window (see the item "Seed rate rule: stateless per action").

## Key files

- `scripts/fetch_run_logs.py` — fetches the logs. Command: `railway run --service SlowBurnBotGamma -- python3 scripts/fetch_run_logs.py --hours 120`.
- `bot-client/burnBot_seeds.py` — `decide_seed_use`, `SeedBook`, `load_seed_pool`, discovery.
- `bot-client/burnBot_followEngagers.py` and `bot-client/burnBot_followGroup.py` — the callers and the saturation metric.
- `app/services/follow_seeds.py` — seed stats per action, the cool-down and the tombstone.
- `tests/` — the unit tests of the seed pool.
- Memory notes `project_follow_target_sourcing` and `project_seed_pool_review_2026_11_25`.
