---
id: 87
title: Verify similar follow filter on v1.221
task: 0087-verify-similar-follow-filter-v1.221
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-11-21
updated: 2026-11-21
---

## Goal

Make sure that the "account list [similar]" action now reads the hover card and applies the
follow filters in real sessions.

## Context

- Before the change, the follow filters never ran for "similar": 81 of 81 candidates had no
  hover card (`-> ok (no-card)`) in the sessions of 2026-11-16 to 2026-11-19. The Similar
  accounts strip of Instagram shows no hover card.
- The change (commit `b4da6a9`, bot v1.220 code, first working image v1.221) opens the "See all"
  link of the strip. This opens the "Suggested for you" dialog, where hover cards render.
  If the dialog does not open, the bot skips the target (zero yield) and tries the next one.
- The change was tested with a fake browser (8 cases) and on the live page (link, dialog, rows,
  scrolling, hover card). No real bot session had run the code at the time of writing.
- All 4 clients run v1.221 since 2026-11-20. Accounts that use the action: theburleybar
  (client 01) and triagespirits (client 03). theburleybar runs on a "random 2/3" schedule.

## Decisions

- No fall back to the strip when the dialog does not open. The strip has no hover cards, so a
  fall back would follow without the filters again.

## Dead ends

None.

## Plan

1. [ ] Fetch the run logs with `railway run --service SlowBurnBotGamma ...` (see memory `project_run_log_review.md`), `--hours 48`, for theburleybar and triagespirits.
2. [ ] In each `[similar]` action, check that the `card [...]` lines show posts, followers and following, and that no line says `no-card`. Count the `[-skip]` lines (too_big, low_ratio, private).
3. [ ] Count the warnings `no See all link in Similar accounts` and `Similar accounts list did not open or has no Follow rows`. If more than about 1 in 3 targets, find the cause on the live page.
4. [ ] Check the follow counts against the settings (5+rand5 for triagespirits, 4+rand4 for theburleybar). A lower count is expected, because the filters now remove accounts.
5. [ ] After about 2 weeks, compare the follow-back of the new "similar" follows with the old 0-7%.

## Key files

- `bot-client/burnBot_followGroup.py` (`_open_similar_list`, `_harvest_similar_accounts`)
- `bot-client/burnBot_followFilter.py` (`screen_candidate`, `evaluate_candidate`)
