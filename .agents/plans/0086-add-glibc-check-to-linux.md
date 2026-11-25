---
id: 86
title: Add glibc check to Linux release build
task: 0086-add-glibc-check-to-linux
repo: ~/projects/SlowBurnBot
branch: main
status: open
created: 2026-11-21
updated: 2026-11-25
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

- Update 2026-11-25 (written, tested locally, not yet run in CI):
  - `scripts/check_glibc.py <binary> --max X.Y` reads each bundled library from the PyInstaller
    archive and takes the highest `GLIBC_X.Y`. Exit 0 = ok, 1 = a library needs more (each is
    listed), 2 = unreadable file or an archive with no library.
  - The workflow step "Check that the runtime image can load the bundled libraries" is in the
    `linux` and `linux-arm64` jobs, after "Build with PyInstaller" and before the image push. The
    limit is the glibc of the image in the FROM line of `bot-client/Dockerfile.linux`
    (`docker run --rm <image> ldd --version`; 2.41 for `python:3.12-slim`). The Dockerfile has CRLF
    line endings, so the step removes the carriage return from the image name.
  - Tested on the binary of the v1.223 image: highest need 2.38 (`libcrypto.so.3`). Limit 2.41 and
    2.38: exit 0. Limit 2.37: exit 1 with 8 libraries named. Missing file and a file that is not an
    archive: exit 2.
  - Not possible: a test with a real v1.220 binary (gone). A low limit stands in for it.

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

1. [x] Write a small script `scripts/check_glibc.py` that takes the binary path and a maximum glibc version, reads each bundled `.so`, and exits 1 with the name of each library that needs more.
2. [x] Add a step to the `linux` job in `.github/workflows/release-bot-client.yml`, after "Build with PyInstaller" and before "Build and push Docker image". The maximum is the glibc of the runtime image (2.41).
3. [x] Add the same step to the `linux-arm64` job.
4. [x] Check that the step fails on a too-new binary and passes on a good one: done locally with the v1.223 binary and a low limit. The first real proof is the next release run.
5. [ ] Release the next bot version and check that both jobs show the step and that it passes (the run log line `check_glibc: N libraries read; highest need: ...`). If the step is wrong, the release fails before the image push (safe, but it blocks the release).
6. [x] Record in the release memory note that the check exists (done 2026-11-25). Remove the manual method from that note after step 5 passes.

## Key files

- `.github/workflows/release-bot-client.yml` (the `linux` and `linux-arm64` jobs)
- `bot-client/Dockerfile.linux` (the runtime base image)
- `bot-client/SlowBurnBot.linux.spec`
