# listmonk for the agent

`tools/listmonk_mcp.py` gives Studio's agent a [listmonk](https://listmonk.app) it can reach (any v5 or newer, over its REST API).
It is one program in two halves, registered as two tool servers so Studio asks about them differently:

| Server | `LISTMONK_TOOLS` | Approval | Tools |
|---|---|---|---|
| `listmonk` | `manage` | follows Auto-run commands | overview, create_list, find_contacts, add_contact, update_contact, change_lists, list_tags, tag_contacts, fill_tag_list, import_contacts, import_status, stop_import, list_templates, list_campaigns, get_campaign, create_campaign, update_campaign, copy_campaign, preview_campaign |
| `listmonk-send` | `send` | **asks before every call** | send_test, start_campaign, schedule_campaign, pause_campaign, cancel_campaign, delete_contacts, send_email, and `restart_listmonk` when `LISTMONK_RESTART` is set |

Nothing in `listmonk` sends an email: campaigns are made as drafts. `start_campaign` and `schedule_campaign`
need `recipients`, the number of contacts the campaign reaches now (not blocklisted, not unsubscribed); a wrong number is
refused with the right one, so the approval card always shows how many people will get it. Starting, scheduling, pausing and
cancelling use listmonk's own `PUT /api/campaigns/<id>/status`. Tests go only to existing contacts (listmonk's rule).
Single emails use a transactional template (listmonk's `tx` type; the tool passes `subject` and `message` as its data).
Contacts files are read only inside `LISTMONK_ROOTS` (default `~/Projects`).
Deleting lists or campaigns is left to listmonk's own pages.

## Registering the servers

`panel.json` in this folder is the same as the JSON below: paste it into Account & settings → Tool servers (or add each half with Add server). Use your own paths, user and token:

```json
{
  "mcpServers": {
    "listmonk": {
      "command": "python3",
      "args": [
        "/path/to/eidoloom-studio/tools/listmonk_mcp.py"
      ],
      "env": {
        "LISTMONK_URL": "http://127.0.0.1:9000",
        "LISTMONK_USER": "studio",
        "LISTMONK_TOKEN": "<the API user's token>",
        "LISTMONK_ROOTS": "/path/to/your/projects",
        "LISTMONK_TOOLS": "manage"
      },
      "approval": "with_commands",
      "description": "listmonk email tools"
    },
    "listmonk-send": {
      "command": "python3",
      "args": [
        "/path/to/eidoloom-studio/tools/listmonk_mcp.py"
      ],
      "env": {
        "LISTMONK_URL": "http://127.0.0.1:9000",
        "LISTMONK_USER": "studio",
        "LISTMONK_TOKEN": "<the API user's token>",
        "LISTMONK_ROOTS": "/path/to/your/projects",
        "LISTMONK_TOOLS": "send",
        "LISTMONK_RESTART": "docker restart listmonk_app",
        "LISTMONK_BACKUP": "docker exec listmonk_db pg_dumpall -U listmonk",
        "LISTMONK_BACKUP_DIR": "/path/to/listmonk-backups"
      },
      "approval": "always",
      "timeout_s": 300,
      "description": "listmonk email tools"
    }
  }
}
```

`LISTMONK_TOOLS` decides which half a program serves: `send` gives the sending tools, anything else (or nothing) the preparing ones.
`LISTMONK_RESTART` and `LISTMONK_BACKUP` are optional: without the first there is no `restart_listmonk`, without the second
`delete_contacts` still works but says there is no backup. Adjust the container names to your own setup.

## Turning it on

1. In listmonk's admin (Users → New), make a user of type **API**, with a role that may manage subscribers, lists,
   campaigns, templates, imports and send transactional mail. Copy its token (shown once).
2. Enter the user's name and the token as `LISTMONK_USER` and `LISTMONK_TOKEN` in both servers, and switch both on.
3. Ask the agent: "Show me what is in listmonk", then for example "Import contacts/parents.csv into a new list
   Parents 2026 and draft a campaign for them".

Both servers are admin-only (not shared with users), since they reach real people's addresses.
The tests use `tests/mock_listmonk.py`, an in-memory listmonk that records what would have been sent.

## Campaigns go to lists

A campaign has lists as its recipients. To email everyone who has a contact tag, call `fill_tag_list` first: it fills a list
called `Name (Group)` (made the first time, with the list tag `contact-tag`) with exactly the contacts that have the tag now,
and then the campaign is made for that list. Run it again before a later campaign, since tags change.

Contact tags live in each contact's attributes (`attribs.tags`, a list of text such as `Berlin/Mitte` or `VIP`). `list_tags`,
`tag_contacts` (add or remove, by email, by list or by another tag) and `find_contacts` with `tag` work on them. Only the
attributes change; lists and subscriptions (unsubscribed ones too) stay.

Open and click tracking are listmonk's own: write `{{ TrackView }}` in the body to count opens and
`{{ TrackLink "https://example.com" }}` for a link whose clicks are counted. They count only when tracking is on in
listmonk's settings. `get_campaign` says whether the body has `{{ TrackView }}`.

`copy_campaign` makes a new draft from an old one (subject, text, template, sender, tags and attachments) for other lists.
It does not check who got the original: anyone on both lists would get the email twice.

## Stuck imports

listmonk runs one import at a time. If an import's database transaction is left open inside listmonk
("idle in transaction" in `pg_stat_activity`), the next import waits on its row locks forever, showing
"importing, 0 of 7975", and every later import is refused. So:

- `import_contacts` waits up to 2 minutes and, when the count has not moved in that time, says the import is stuck,
  not slow, and to call `stop_import`. While another import runs it says to use `import_status`, not sleep.
- `import_status` and `stop_import` report and stop the running import (listmonk's
  `DELETE /api/import/subscribers`); contacts already in stay.
- If listmonk cannot stop it, `stop_import` says to call `restart_listmonk` or ask the user. `restart_listmonk` exists
  in the send half only when `LISTMONK_RESTART` is set, so the user approves every restart; it waits until listmonk
  answers again (`/api/health`).
- A CSV file is checked before upload: only the columns email, name and attributes, valid emails, attributes a JSON
  object, every row as long as the header. A wrong file is refused with the fix named.
- Yes/no arguments sent as text ("True", "false") count.

Test knobs: `LISTMONK_STALL_S`, `LISTMONK_IMPORT_WAIT_S`, `LISTMONK_STOP_WAIT_S`; the fake listmonk hangs an import
with `POST /mock/stuck?mode=soft|hard` and `LISTMONK_RESTART` calls `/mock/restart`.

## Deleting contacts

`delete_contacts` (send half, so the user approves every call) deletes contacts for good: everyone in the given lists,
except anyone also in a list given as `keep`. Like `start_campaign`, it needs `count`, the exact number it deletes;
a wrong count deletes nothing and gives the real one, so the approval card shows the true number. When `LISTMONK_BACKUP` is
set it saves listmonk's whole database first into `LISTMONK_BACKUP_DIR` and deletes nothing if that fails. Restore a
`pg_dumpall` file with `docker exec -i listmonk_db psql -U listmonk -d postgres < FILE` (into a fresh database). Deletes go
500 ids at a time (`DELETE /api/subscribers?id=...`).
