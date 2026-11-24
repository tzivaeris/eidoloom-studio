# Security

Eidoloom Studio gives an AI agent, and every person who can sign in, the power to change files and run programs.
Treat it like a shell on your machine: keep it on your own network, give accounts only to people you trust with that
power, and know what the sandbox does and does not protect.

## The short version

- **Do not put Studio on the open internet.** Keep it on `127.0.0.1` or your local network. For remote access, use a
  VPN, an SSH tunnel, or a web server with HTTPS that only you can reach.
- **An admin's folders without the sandbox are the admin's account.** The agent and the terminal run commands as the
  user Studio runs as, with everything that user can read and write.
- **Users are always sandboxed**, but tool servers and the model server are shared, and the sandbox has limits (below).
- **The agent asks before each edit and command** unless you switch on auto-apply or auto-run. A model can be misled
  by what it reads (a file, a web page, a tool's output), so read what it asks to run before you press Run.

## Who can do what

| Who | What they can do |
| --- | --- |
| Admin | Everything: open any folder in `workspace_roots`, run commands on the host (sandbox off), add tool servers, invite and manage users, change the model server, install tools. |
| User | Their own private folder under `user_root`, and folders shared with them, with the rights the owner gave (files, agent, terminal, delete, share). Their commands and terminals always run in a sandbox. They see only the tool servers an admin shared with them. |
| Anyone else | The sign-in page, and the pages and pictures behind preview and live links while those links last (an hour). |

## What protects the page

- Passwords are stored as PBKDF2-SHA256 hashes (200,000 rounds). Sessions, invite links and reset links are stored only
  as hashes, so a copy of `data/` is not a way in.
- The session cookie is `HttpOnly` and `SameSite=Strict`, and `Secure` behind an HTTPS proxy. Every change needs the
  `X-Studio: 1` header, and a request whose `Origin` differs from its `Host` is refused.
- Five failed sign-ins from one address in a minute are refused for that minute.
- Files are confined to the open folder by their real path, so `..` and links that lead out are refused.
- Pages, pictures and live servers from a project are shown from a separate address with a one-hour token, inside a
  CSP sandbox with no access to Studio's origin or cookie. Their scripts cannot act as you. They can still navigate
  their own frame to another site, which could carry away what they show.

## What the sandbox protects

With the sandbox on, the agent's commands and the terminals of that folder run in the folder's own Docker container:

- as your user id, never root, with every Linux capability dropped and `no-new-privileges`;
- with limits on memory, CPUs and processes;
- seeing only the project folder (as `/work`) and a home folder of its own, not the rest of your files;
- with the internet off, on only for commands that install packages (`npm install`, `pip install` and the like), or
  on. Which mode a folder uses is the folder's setting.

Git runs inside the container too, so a hostile repository's hooks and settings cannot reach the host.

## What the sandbox does not protect

- **The project folder itself.** The container can read and change everything in the project, including its `.git`
  and any secrets in `.env` files.
- **Your network, when the internet is on.** A container with internet can also reach this machine's other services
  and your local network, unless you apply `sudo bash sandbox/firewall.sh apply` (and `install-boot` to keep the rules
  after a reboot). The rules block private addresses and leave the internet and DNS open.
- **Docker.** The user Studio runs as must be in the `docker` group, which is as powerful as root on that machine.
  Anyone who takes over that account, or finds a way out of a container, controls the machine.
- **Tool servers.** Every tool server (MCP) runs on the host as Studio's user, even for sandboxed folders. Share with
  users only the servers you would let them use directly, and set servers that change things to "Ask before every
  call".
- **The model.** The agent sends your files' contents to the model server you connect. A local server keeps them on
  your machine; a remote or cloud one does not.
- **Folders without the sandbox.** An admin's folder with the sandbox off runs every command on the host, as Studio's
  user.

## Secrets Studio keeps

Everything is in `data/` beside `studio.py`, which is never served:

- `users.json`, `auth_sessions.json`, `auth_tokens.json`: password and token hashes.
- `mcp.json`: tool servers' settings, including their keys (written readable by Studio's user only). The page shows
  only the last four characters of a secret.
- `studio.config.json` holds `gateway_key` when you set one, and is then readable by Studio's user only.
- Chats, attachments and undo points: what you and the agent wrote.

`backups/` holds daily copies of `data/`, with the same secrets. Keep both out of shared folders and synced drives.

## Optional parts with their own risks

- **A person's own computer** (`studio-helper`): the helper connects out to Studio only, asks before trusting a
  certificate it does not know, and checks every path against the folder it shares. Commands the agent runs there run
  as that person, with no sandbox.
- **Codex** (admins only, sandbox off): Studio starts OpenAI's Codex CLI with this machine's ChatGPT login. Studio
  never reads that login's files.
- **The agent's web browser**: a Chromium whose proxy refuses private addresses. Pages it visits can still try to
  mislead the model.

## Reporting a problem

Please report security problems privately to the maintainer rather than in a public issue: <!-- security contact -->
the contact address will be listed here before the first public release. Include what you did, what happened, and the
version (the commit) you ran.
