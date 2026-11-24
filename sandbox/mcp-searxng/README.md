# Private web search and page fetching for Eidoloom Studio

Set up on 2026-11-09 so models can search the web and read pages through MCP, without an account or API key.

- **SearXNG**, a self-hosted search engine that asks other engines (Brave, Google and others) and returns their results:
  container `studio-searxng` (image `searxng/searxng`), reachable only from this machine on `127.0.0.1:8888`, restarting
  with Docker. `config/settings.yml` (private) turns on JSON answers, which the MCP server needs (otherwise SearXNG answers
  403), turns off the public rate limiter and holds the secret key. Check it: `curl 'http://127.0.0.1:8888/search?q=test&format=json'`.
- **MCP servers** (`panel.json`, to paste into Studio's Tool servers panel):
  - `web-search`: `npx -y mcp-searxng` with `SEARXNG_URL`; tools `searxng_web_search` and `web_url_read`.
  - `fetch`: the reference Fetch server in Docker (`mcp/fetch`, 1.23.0), one tool, `fetch`, which returns a page as Markdown.
    It runs with Docker's own network, so it reaches the internet and the LAN, but not services bound to 127.0.0.1 here.
- Pages and search results can carry text meant to steer the model. Keep both servers on "Ask before every call", or at
  least check what a run fetched.
- Images were pulled with `DOCKER_CONFIG=$(mktemp -d) docker pull ...`, because this machine's Docker cannot pull by itself
  (see `../mcp-sample-db/README.md`).
- Remove it all: remove both servers in the panel, `docker rm -f studio-searxng`, then delete this folder.
