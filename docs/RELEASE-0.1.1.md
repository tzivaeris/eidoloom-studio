# Eidoloom Studio 0.1.1

Fixes for installing from a fresh clone, a safer default address, and much fuller documentation. Upgrading from 0.1.0
is `git pull` and `./install.sh` again.

## Fixed

- **`./install.sh` works from a fresh clone or a ZIP download.** In 0.1.0 it stopped at the end with "Permission
  denied", because `install-service.sh` was not executable; it now runs it through `bash`, and the scripts are
  executable again.
- **Studio listens on `127.0.0.1` unless you say otherwise.** A `studio.config.json` without `host` used to open Studio
  to the whole network. Set `"host": "0.0.0.0"` when you want other computers to reach it.
- **The install prints the address Studio really listens on,** not the machine's network address.

## Changed

- **The service is `eidoloom-studio`** (it was `strata-studio`). `./install.sh` and `install-service.sh` stop and
  remove the old service and install the new one, so the two never hold the same port. Restart it with
  `systemctl --user restart eidoloom-studio`.
- **About and the guide** describe Studio as an AI agent harness and name Strata among the model servers. The guide
  opens in English unless you pick Greek.

## Documentation

- **How it fits together:** an architecture diagram in the README.
- **Optional tools:** every tool and the project it is built on, linked.
- **Folders on your own computer:** how to connect a folder on a Windows or Linux computer with `studio-helper`.
- **No model server yet?** Trying Studio with OpenAI's Codex.
- `docs/MAINTAINER.md` maps the sections of `studio.py` and `web/app.js`, which now have a banner for each area.

## Tests

The backend tests and, new, the browser tests in real Chromium run on GitHub on every push. A failing check shows on
the run's page without opening the log.

## Files

`studio-helper.exe` (Windows) and `studio-helper` (Linux) are the helper, version 0.08, unchanged since 0.1.0, that
connects a folder on a person's own computer; put them in `tools/helper/dist/` on the server (the README's "Folders on
your own computer"). `SHA256SUMS` checks them.

## Known limits

- The server runs on Linux only (developed on Ubuntu 24.04). Folders can also be on Windows or Linux computers,
  through `studio-helper`.
- The editor and terminal load from cdn.jsdelivr.net, so the browser needs to reach it.
- The picture, voice, music and video engines in Tools & setup are written for AMD graphics cards (ROCm).
