# MailSender (listmonk) for the agent

`tools/listmonk_mcp.py` gives Studio's agent the local listmonk that MailSender runs
(`~/Projects/Tools/MailSender`, listmonk v6 at http://127.0.0.1:9100). It is one program in two halves,
registered as two tool servers so Studio asks about them differently:

| Server | `LISTMONK_TOOLS` | Approval | Tools |
|---|---|---|---|
| `mailsender` | `manage` | follows Auto-run commands | overview, create_list, find_contacts, add_contact, update_contact, change_lists, import_contacts, list_templates, list_campaigns, get_campaign, create_campaign, update_campaign, copy_campaign, check_already_sent, sending_capacity, preview_campaign |
| `mailsender-send` | `send` | **asks before every call** | send_test, start_campaign, schedule_campaign, pause_campaign, cancel_campaign, send_email |

Nothing in `mailsender` sends an email: campaigns are made as drafts. `start_campaign` and `schedule_campaign`
need `recipients`, the number of contacts the campaign reaches now; a wrong number is refused with the right one,
so the approval card always shows how many people will get it. Tests go only to existing contacts (listmonk's rule).
Single emails use a transactional template (`Newsletter – transactional`, which reads `.Tx.Data.subject`
and `.Tx.Data.message`). Contacts files are read only inside `LISTMONK_ROOTS` (default `~/Projects`).
Deleting contacts, lists or campaigns is left to MailSender's own pages.

## Turning it on

1. In listmonk's admin (http://127.0.0.1:9100/admin → Users → New), make a user of type **API**, with a role that may
   manage subscribers, lists, campaigns, templates, imports and send transactional mail. Copy its token (shown once).
2. In Studio: Account & settings → Tool servers → `mailsender` → set `LISTMONK_USER` to the API user's name and
   `LISTMONK_TOKEN` to the token; the same in `mailsender-send`. Switch both on.
3. Ask the agent: "Show me what is in MailSender", then for example "Import contacts/parents.csv into a new list
   Γονείς 2026 and draft a campaign for them".

Both servers are admin-only (not shared with users), since they reach real people's addresses.

The tests use `tests/mock_listmonk.py`, an in-memory listmonk that records what would have been sent.

## Stuck imports (2026-11-13)

listmonk runs one import at a time. On 2026-11-13 an import's database transaction was left open inside listmonk
("idle in transaction" in `pg_stat_activity`), so the next import waited on its row locks forever, showing
"importing, 0 of 7975", and every later import was refused. Keystone could only sleep and retry. Now:

- `import_contacts` waits up to 2 minutes and, when the count has not moved in that time, says the import is stuck,
  not slow, and to call `stop_import`. While another import runs it says to use `import_status`, not sleep.
- `import_status` and `stop_import` (manage half) report and stop the running import (listmonk's
  `DELETE /api/import/subscribers`); contacts already in stay.
- If listmonk cannot stop it, `stop_import` says to call `restart_listmonk` or ask the user. `restart_listmonk` exists
  in the send half only when `LISTMONK_RESTART` is set (here `docker restart listmonk_app`), so the user approves every
  restart; it waits until listmonk answers again (`/api/health`).
- A CSV file is checked before upload (`check_csv`): only the columns email, name and attributes, valid emails,
  attributes a JSON object, every row as long as the header. Keystone's `attribs` column and its one-column-per-field
  file (the one that left listmonk hanging) are refused with the fix named.
- Yes/no arguments sent as text ("True", "false") count: before this, `"overwrite": "True"` ran without overwrite.
- Studio itself adds a note to the third wait-only command in a run (`WAIT_COMMAND`, `WAIT_NOTE`, with `turn_guard`).

Test knobs: `LISTMONK_STALL_S`, `LISTMONK_IMPORT_WAIT_S`, `LISTMONK_STOP_WAIT_S`; the fake listmonk hangs an import
with `POST /mock/stuck?mode=soft|hard` and `LISTMONK_RESTART` calls `/mock/restart`.

## Categories and sub-categories (2026-11-13)

MailSender groups lists by tags: `cat:<category>` and, under it, `sub:<sub-category>` (usually a city), as its
`ui/src/app/core/categories.ts` does. listmonk turns spaces in tags into hyphens (`sub:ΑΓΙΑ-ΣΟΦΙΑΣ`), so the tools
write spaces in group names as no-break spaces, which it keeps and MailSender shows as spaces (`group_tag`). MailSender's own
forms still lose them. The tools follow it: `create_list` takes `category` and `subcategory`;
`set_list_group` moves lists (a value left out stays, an empty one is removed), like MailSender's Move to category;
`overview` shows each list as `· Category › Sub-category` with the other tags apart; and wherever a tool takes `lists`
(imports, campaigns, list changes), a whole group may be named: `Γονείς › Θεσσαλονίκη`, `› Θεσσαλονίκη` (that city in
every category), `cat:Γονείς` or `sub:Θεσσαλονίκη`.

## Deleting contacts (2026-11-13)

`delete_contacts` (send half, so the user approves every call) deletes contacts for good: everyone in the given lists or
groups, except anyone also in a list given as `keep`. Like `start_campaign`, it needs `count`, the exact number it deletes;
a wrong count deletes nothing and gives the real one, so the approval card shows the true number. Before deleting it saves
MailSender's whole database with `LISTMONK_BACKUP` (here `docker exec listmonk_db pg_dumpall -U listmonk`) into
`LISTMONK_BACKUP_DIR` (Studio's `data/mailsender-backups/`), and deletes nothing if that fails. Restore with
`docker exec -i listmonk_db psql -U listmonk -d postgres < FILE` (into a fresh database). Deletes go 500 ids at a time
(`DELETE /api/subscribers?id=…`). Added after Keystone, asked to delete the city contacts with no tool for it, tried to reach
listmonk from the user's PC and searched its drives for listmonk's settings file; the tool servers' prompt now says to ask
instead.

## Second rounds and the daily limit (2026-11-20)

Asked to "send the rest of the subscribers of a campaign over the weekend", the tools could not tell who already got
it, nor how much Gmail's limit allowed, and they started campaigns straight in listmonk, past MailSender's checks (a
pause there even looked to MailSender's pacer like Gmail refusing, which blocks sending for 24 hours). Now:

- `check_already_sent` (manage): given earlier campaigns and some recipients (lists, `to_tags` or a campaign), how many
  already got them, or are still to get them (paused or scheduled), and how many have not. It reads MailSender's
  record of who each campaign reached (`GET /pacer/continues/<ids>`).
- `create_campaign` / `update_campaign` take `continues` (earlier campaigns): the hidden tag `ms:continues:<id>`, the
  same as MailSender's editor. MailSender leaves those people out when the campaign is started or scheduled, before
  the pacer resumes it, and every 20 s while it waits. Only a campaign that started (or is scheduled) can be continued.
  Both say how many are left out, and warn when a campaign of the last 60 days with the same subject or name (a copy)
  already reached some of the recipients, naming the `continues` that leaves them out.
- `sending_capacity` (manage): MailSender's pacer: the limit, room now, a block after Gmail refused, what running,
  paused and scheduled campaigns still have to send, and with `emails` when that many more would have gone out
  (from the pacer's `frees`, every upcoming hour). The pacer spreads a campaign over days by itself: no batches.
- `start_campaign`, `schedule_campaign`, `pause_campaign`, `cancel_campaign` go through MailSender
  (`PUT MAILSENDER_URL/api/campaigns/<id>/status`, same API user), so its checks run. When there is no room, MailSender
  holds the campaign (409) and the tool says the pacer starts it by itself. MailSender lets API users pause, but cancel
  only for its admins: the tool then says to ask the user. `start_campaign`'s `recipients` for a continuation is the count
  without who already got it.
- `copy_campaign` (manage): the same email to other recipients, like MailSender's Αντίγραφο: subject, text, template,
  sender, attachments and open-tracking settings stay; by default it continues the original and what that continued.
- A campaign can continue a draft or scheduled one (the first round first, then the rest): MailSender leaves out
  whoever that one will reach; the tool fills that draft's helper list (its areas and people) so the counts are right.
- Edits keep a campaign's attachments (`media`), and its hand-picked people (`ms:to-sub:<id>`, through its helper list).

This needs MailSender's helper of 2026-11-20 or later, which accepts listmonk API users (`Authorization: token …`) as
well as browser sessions. Tested against the fake listmonk (it plays these MailSender routes too) and read-only
against the real MailSender with the `studio` API user: the counts for campaigns #22, #24 and #29 match what was
found by hand; nothing was sent or changed.
