---
id: 86
title: Add glibc check to Linux release build
task: 0086-add-glibc-check-to-linux
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-11-21
updated: 2026-11-21
---

## Goal

The release build must fail if the Linux client binary needs a newer glibc than the runtime
image has. A green release run must mean that the image can start.

## Context

- On 2026-11-20 the v1.220 amd64 binary was built on `ubuntu-latest`. GitHub had changed this
  runner to Ubuntu 26.04 (glibc 2.42). The runtime image `python:3.12-slim` (Debian trixie)
  has glibc 2.41. The container stopped at start with `GLIBC_2.42 not found` (required by the
  bundled `termios` module). The release run was green, and the server synced to 1.220.
- Corrected by v1.221: the `linux` job in `.github/workflows/release-bot-client.yml` runs on
  `ubuntu-24.04`. The bundled libraries of v1.221 need glibc 2.38 at most.
- Nothing stops the same fault if the runner or the base image changes. The arm64 job already
  uses the pinned runner `ubuntu-24.04-arm`.
- Done by hand on 2026-11-20: copy the binary out of the image (`docker create`, `docker cp`),
  read each bundled `.so` with `PyInstaller.archive.readers.CArchiveReader`, take the highest
  `GLIBC_2.N`. The script was in the scratchpad and is lost.

## Decisions

- Check the bundled libraries, not the binary as text. The onefile bundle is compressed, so a
  text scan of `/app/slowburnbot` shows only `GLIBC_2.14`.
- Check in the CI job, before the image push, so a bad build never reaches `:latest`.
  The release script syncs the server version after the build, so the check must fail the build.

## Dead ends

- `strings` on the binary: the image has no `strings`, and the bundle is compressed.
- A test start of the binary (`docker run ... /app/slowburnbot <config>`): it stops at the
  activation prompt, before `textual` imports `termios`. v1.220 and v1.221 both started.

## Plan

1. [ ] Write a small script `scripts/check_glibc.py` that takes the binary path and a maximum glibc version, reads each bundled `.so`, and exits 1 with the name of each library that needs more.
2. [ ] Add a step to the `linux` job in `.github/workflows/release-bot-client.yml`, after "Build with PyInstaller" and before "Build and push Docker image". The maximum is the glibc of the runtime image (2.41).
3. [ ] Add the same step to the `linux-arm64` job.
4. [ ] Check that the step fails on a v1.220 style binary (build on a newer runner, or use a copy of the old binary) and passes on v1.221.
5. [ ] Record in the release memory note that the check exists, and remove the manual method.

## Key files

- `.github/workflows/release-bot-client.yml` (the `linux` and `linux-arm64` jobs)
- `bot-client/Dockerfile.linux` (the runtime base image)
- `bot-client/SlowBurnBot.linux.spec`
