---
id: 12
title: Liker seeds mostly exhausted
task: 0012-liker-seeds-mostly-exhausted
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-10-14
updated: 2026-11-01
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

## Decisions

- The user chose a retire rule for exhausted liker seeds. The other option was seed rotation.

## Dead ends

None.

## Plan

1. [ ] Check the `account_group_mode` of `timeforashifty` (unknown on 2026-11-01).
2. [ ] Proposal, for the user to decide: make the retire rule apply to accounts in `manual` mode. The other option is to move `timeforashifty` to `pool` mode.
3. [ ] Proposal, for the user to decide: lower `SATURATION_RETIRE` from 80 in `bot-client/burnBot_seeds.py`.

## Key files

- `scripts/fetch_run_logs.py` — fetches the logs. Command: `railway run --service SlowBurnBotGamma -- python3 scripts/fetch_run_logs.py --hours 120`.
- `bot-client/burnBot_seeds.py` — `SATURATION_RETIRE` and `finish_seed_use`.
- `bot-client/burnBot_followEngagers.py` — the call of `finish_seed_use` (about line 436).
- `bot-client/burnBot_accountSession.py` — the read of `account_group_mode` (default `manual`).
- Memory note `project_follow_target_sourcing` — how follow seeds and the self-tuning `follow_seeds` pool work.
