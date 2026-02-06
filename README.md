# BeeLocate Pro

## PDF Export (Render)
PDF export uses headless Chromium (`--print-to-pdf`). On Render you must run the app as a Docker service so Chromium exists in the container.
This repo includes a `Dockerfile` that installs `chromium` and fonts.

Render steps:
1) Create **New Web Service**
2) Select this repo
3) Environment: **Docker**
4) Deploy

After deploy, `/report/<rid>.pdf` should work.
