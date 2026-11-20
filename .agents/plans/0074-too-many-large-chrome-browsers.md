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
- How the bots behaved after the kill is not checked yet.
- Rules in `~/services/slowburn/CLAUDE.md`: never start or restart a client container from an
  agent (the TUI needs the user); an agent may stop a container (`docker stop`). The clients
  have the restart policy `unless-stopped`.

## Decisions

None.

## Dead ends

None.

## Plan

1. [ ] Check how each bot behaved after the browsers were killed on 2026-11-20 (its TUI with `docker attach`, detach with Ctrl-P Ctrl-Q; `docker logs`), and if it recovered.
2. [ ] Find why a client starts a second `chromedriver` and Chrome while the first one is still open.
3. [ ] Find why each browser uses 0.5–0.75 GB (open tabs, the profile in `clientNN-data/ChromeUserData/`, Chrome flags), and reduce it.

## Key files

- `~/services/slowburn/CLAUDE.md` — the client containers on FinchServ, and the rules for agents.
- `~/services/slowburn/compose.yml` — the client and sidecar containers.
- `~/services/slowburn/clientNN-data/` — the bot data and `ChromeUserData/` (root owns it; never in git).
