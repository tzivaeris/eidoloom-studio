# Sample database for MCP tests

A made-up Greek tutoring centre (`schema.sql`: tutors, rooms, students, lessons, lesson_students, payments) in its own
Postgres container, for trying Postgres MCP Pro with Eidoloom Studio without touching a real database. Created on 2026-11-09.

- Container `studio-sample-postgres` (image `postgres:16-alpine`), database `tutoring`, reachable only from this machine on
  `127.0.0.1:55433`. It does not restart after a reboot: `docker start studio-sample-postgres`.
- `studio_ro` can only read (SELECT on every table, plus `pg_read_all_stats` for the health and index tools).
- `credentials.env` (private) holds the test passwords. `panel.json` (private) is the JSON to paste into Studio's
  Tool servers panel; it runs `crystaldba/postgres-mcp` in Docker with `--access-mode=restricted` and `--network host`.
- Docker on this machine cannot download images by itself: `~/.docker/config.json` names `"credsStore": "desktop"`, a Docker
  Desktop helper that is not installed, so the panel's first start failed with "the server program exited". The public image was
  pulled once with an empty settings folder instead: `DOCKER_CONFIG=$(mktemp -d) docker pull crystaldba/postgres-mcp`
  (version 1.6.0, 414 MB; 9 tools checked with a real MCP handshake). The same applies to any other Docker-based MCP server.
- Remove it all: `docker rm -f studio-sample-postgres`, then delete this folder and the server in the panel.
