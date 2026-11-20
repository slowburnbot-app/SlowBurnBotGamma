---
id: 74
title: Too many large Chrome browsers per client
task: 0074-too-many-large-chrome-browsers
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-11-20
updated: 2026-11-20
---

## Goal

Find out why the SlowBurnBot clients on FinchServ run more than 1 Chrome browser each, and why
each browser is so large. Then reduce both, so that the clients do not cause memory pressure on
FinchServ.

## Context

- On 2026-11-20, at about 17:00, FinchServ was short of memory:
  - 29 of 31 GiB in use, and the 2 GiB swap was full.
  - The load average was 203. The memory pressure "full" was 59–76%.
  - `tailscaled` failed and restarted at 16:49:13.
- The 4 client containers (`slowburn-client01` to `04`, image
  `ghcr.io/slowburnbot-app/slowburnbotgamma/slowburnbot-client:latest`, up 7–8 hours) ran
  **7 Chrome browsers**:
  - `client01`: 2, started 10:04 and 10:44.
  - `client02`: 1, started 11:20.
  - `client03`: 2, started 10:14 and 10:33.
  - `client04`: 2, started 09:53 and 10:09.
- Each browser had its own `chromedriver` (Selenium), and each driver had 1 open connection from
  the bot. Thus the bot held both drivers of a client. They were not abandoned browsers.
- Each browser with its child processes used 466–747 MB, about 3.9 GiB in all. The other large
  users at that time: ComfyUI 8.3 GiB, 28 `claude` processes 7.1 GiB, and Node MCP servers 3.9 GiB.
- At about 17:20 the user decided to close the browsers:
  - The agent sent TERM to each top Chrome process with `docker exec <client> kill`, and KILL
    for the `client04` browser of 09:53, which did not stop on TERM.
  - The containers kept running, with no restart.
  - The `chromedriver` processes stayed, with "defunct" Chrome children.
  - In the first minutes, no bot started a new browser.
  - The available memory went from 1.4 GiB to 5.5 GiB.
- How the bots behaved after the kill is not observed yet. The code opens a new browser at the
  next session if the old one does not answer (`bot-client/burnBot_accountSession.py`, lines
  164–177).
- **Why many (found 2026-11-20):** each account has its own browser and `chromedriver`. Thus a
  client with 2 accounts runs 2 browsers (client01, 03, 04), and it is not a leak. All 4 clients
  had `close_browser_after_session = False` in `[browser-session]` of
  `~/services/slowburn/clientNN-data/burnBot_config.ini`. Thus each browser stayed open between
  sessions. The code default is `True` (`burnBot_accountSession.py`, line 588), but
  `bot-client/burnBot_config.ini.example` sets `False`.
- **Change (2026-11-20):** all 4 client configs now have `close_browser_after_session = True`.
  The backups are `clientNN-data/burnBot_config.ini.bak-20261120`. The client loads the config
  only at start (`burnBot.py`, lines 181 and 193). Thus the change works after the user starts
  each client again with `update-client.sh`. The record is `~/services/slowburn/CLAUDE.md`,
  "Browsers" (commit `e600b32`).
- Rules in `~/services/slowburn/CLAUDE.md`: never start or restart a client container from an
  agent (the TUI needs the user); an agent may stop a container (`docker stop`). The clients
  have the restart policy `unless-stopped`.

## Decisions

- Close each browser after its session: `close_browser_after_session = True` in all 4
  clients (user, 2026-11-20).

## Dead ends

None.

## Plan

1. [ ] Check how each bot behaved after the browsers were killed on 2026-11-20 (its TUI with `docker attach`, detach with Ctrl-P Ctrl-Q; `docker logs`), and if it recovered.
2. [x] Find why a client runs more than 1 `chromedriver` and Chrome: 1 browser for each account, kept open by `close_browser_after_session = False`.
3. [x] Set `close_browser_after_session = True` in all 4 client configs (2026-11-20).
4. [x] Start each client again with `bash ~/services/slowburn/update-client.sh slowburn-clientNN`, so that the setting works. Done 2026-11-20 at 17:31 by the agent at the user's request, in the Herdr pane of each client (method in `~/services/slowburn/CLAUDE.md`). All 4 sidecars healthy, exits correct, all accounts at "max runs [3/3]" for the day.
5. [ ] At the next runs, check that each browser closes after its session (the log line "browser closed after session" is a debug line; also count the Chrome processes with `ps -u root -o comm= | grep -c chrome`).
6. [ ] Find why each browser uses 0.5–0.75 GB (open tabs, the profile in `clientNN-data/ChromeUserData/`, Chrome flags), and reduce it.
7. [ ] Decide if `bot-client/burnBot_config.ini.example` should also set `close_browser_after_session = True` (a bot-client change needs a `BOT_VERSION` bump).

## Key files

- `~/services/slowburn/CLAUDE.md` — the client containers on FinchServ, and the rules for agents.
- `~/services/slowburn/compose.yml` — the client and sidecar containers.
- `~/services/slowburn/clientNN-data/` — the bot data and `ChromeUserData/` (root owns it; never in git).
