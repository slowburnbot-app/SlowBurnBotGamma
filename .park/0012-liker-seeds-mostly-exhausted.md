---
id: 12
title: Liker seeds mostly exhausted
repo: ~/projects/SlowBurnBot
branch: main
created: 2026-10-14
updated: 2026-10-14
---

## Goal
Stop the bot from wasting time on liker seeds that have no new people left.

## Current state
- A review of the run logs from 2026-10-09 to 2026-10-14 (bot v1.210) found the problem.
- 1,489 of 1,920 `likers` entries (about 78%) were skipped as "in database".
- The same usernames repeat across sessions.
- Some `timeforashifty` seeds were scanned 4 to 5 times: `steadfast_screen_printing`, `fell.1.earth`.
- The pool of new people from these seeds is mostly exhausted.
- No code change is made. This item is a finding only.

## Decisions
None.

## Dead ends
None.

## Next step
Choose a fix: seed rotation, or a retire rule for a seed with a high "in database" skip rate.
`follow[likers-accounts]` already retires a seed when its follow-back percentage is low. A new rule can use the same place.

## Key files
- `scripts/fetch_run_logs.py` — fetches the logs. Command: `railway run --service SlowBurnBotGamma -- python3 scripts/fetch_run_logs.py --hours 120`.
- Memory note `project_follow_target_sourcing` — how follow seeds and the self-tuning `follow_seeds` pool work.
