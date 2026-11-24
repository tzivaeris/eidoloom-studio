#!/usr/bin/env python3
"""An MCP server (stdio) for listmonk (https://listmonk.app): contacts, lists, imports, campaigns and email.

It runs in one of two halves, so Studio can ask about them differently:
  LISTMONK_TOOLS=manage (default)  looks and prepares: lists, contacts, imports, templates, draft campaigns, previews.
                                   Nothing here sends an email.
  LISTMONK_TOOLS=send              sends: a test of a campaign, starting, scheduling, pausing or cancelling one, and a
                                   single email, and (with LISTMONK_RESTART set, e.g. "docker restart listmonk_app")
                                   restarting listmonk when an import is stuck. Give this half the "ask before every call" setting.
Campaigns go to lists. Contacts can also carry tags of your own (kept in attribs.tags, e.g. "Berlin/Mitte"): list_tags,
tag_contacts, find_contacts' tag and fill_tag_list, which fills a list with everyone who has a tag so a campaign can go to it.
It talks to listmonk's API at LISTMONK_URL (default http://127.0.0.1:9000) as an API user (Users → New → API user
in listmonk), LISTMONK_USER and LISTMONK_TOKEN. Contacts files are read only inside LISTMONK_ROOTS (colon-separated,
default ~/Projects).
"""
import base64
import csv
import io
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("LISTMONK_URL", "http://127.0.0.1:9000").rstrip("/")
USER, TOKEN = os.environ.get("LISTMONK_USER", ""), os.environ.get("LISTMONK_TOKEN", "")
HALF = os.environ.get("LISTMONK_TOOLS", "manage")
ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in (os.environ.get("LISTMONK_ROOTS") or "~/Projects").split(":") if p]
PROTOCOL = "2025-06-18"
MAX_FILE = 20 * 1024 * 1024
MAX_ROWS = 5000
# Who a campaign reaches, the way listmonk picks them: not blocklisted, and not unsubscribed from the list.
RECEIVERS = "subscribers.status != 'blocklisted' AND subscriber_lists.status != 'unsubscribed'"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# An import that shows no progress this long is stuck, not slow (listmonk counts rows as it goes).
STALL_S = float(os.environ.get("LISTMONK_STALL_S") or 120)
IMPORT_WAIT_S = float(os.environ.get("LISTMONK_IMPORT_WAIT_S") or 120)
STOP_WAIT_S = float(os.environ.get("LISTMONK_STOP_WAIT_S") or 30)
RESTART = os.environ.get("LISTMONK_RESTART", "").strip()      # e.g. "docker restart listmonk_app"; restart_listmonk exists only with it
SEEN = {}                                                     # the import last seen: its name, total, count and since when unchanged


def yes(value, default=False):
    """A yes/no argument; models send "True", "true", "yes" or 1 as often as true."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y", "on")
    return bool(value)


class Fail(Exception):
    pass


# ---------------------------------------------------------------- listmonk's API
def api(method, path, body=None, query=None, raw=False, form=None, timeout=60):
    if not USER or not TOKEN:
        raise Fail("listmonk is not set up for the agent yet: an admin must create an API user in listmonk "
                   "(Users → New → API user) and enter its name and token as LISTMONK_USER and LISTMONK_TOKEN in "
                   "Studio's Tool servers.")
    url = URL + "/api" + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    headers = {"Authorization": "token " + USER + ":" + TOKEN, "Accept": "application/json"}
    data = None
    if form is not None:
        boundary = "studio" + secrets.token_hex(12)
        parts = []
        for name, value in form.items():
            if isinstance(value, tuple):          # (file name, bytes, type)
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{value[0]}"\r\n'
                             f"Content-Type: {value[2]}\r\n\r\n".encode() + value[1] + b"\r\n")
            else:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            text = json.loads(text).get("message") or text
        except ValueError:
            pass
        if e.code in (401, 403):
            raise Fail(f"listmonk refused the API user ({e.code}: {text}). Check LISTMONK_USER and LISTMONK_TOKEN, and that "
                       "the API user's role allows this.")
        raise Fail(f"listmonk said: {text} (HTTP {e.code})")
    except (urllib.error.URLError, OSError) as e:
        raise Fail(f"listmonk at {URL} did not answer ({getattr(e, 'reason', e)}). Check that listmonk is running "
                   "and that LISTMONK_URL is right.")
    if raw:
        return payload.decode("utf-8", "replace")
    return json.loads(payload or b"{}").get("data")


def paged(path, query=None):
    data = api("GET", path, query={**(query or {}), "per_page": (query or {}).get("per_page", "all")})
    return (data or {}).get("results") or [], (data or {}).get("total", 0)


def all_lists():
    return paged("/lists", {"order_by": "name", "order": "asc"})[0]


def list_ids(names):
    """List ids from names or ids; an unknown one is an error that names the lists there are."""
    if names in (None, "", []):
        return []
    if not isinstance(names, list):
        names = [names]
    lists, out = all_lists(), []
    for item in names:
        found = next((l for l in lists if str(l["id"]) == str(item).strip() or l["name"].lower() == str(item).strip().lower()), None)
        if not found:
            raise Fail(f"there is no list {item!r}. Lists: " + (", ".join(f"{l['name']} (id {l['id']})" for l in lists) or "none yet"))
        out.append(found["id"])
    return sorted(set(out))


def campaign_of(ref):
    if ref in (None, ""):
        raise Fail("say which campaign, by id or name")
    if str(ref).strip().isdigit():
        return api("GET", f"/campaigns/{int(ref)}")
    found = [c for c in paged("/campaigns", {"query": str(ref)})[0] if c["name"].lower() == str(ref).strip().lower()]
    if len(found) != 1:
        raise Fail(f"no single campaign is called {ref!r}" + (f" ({len(found)} are)" if found else "") + "; use its id from list_campaigns")
    return api("GET", f"/campaigns/{found[0]['id']}")


def template_id(ref, kind="campaign"):
    templates = api("GET", "/templates", query={"no_body": "true"}) or []
    usable = [t for t in templates if (t["type"] in ("campaign", "campaign_visual")) == (kind == "campaign")]
    if ref in (None, ""):
        default = next((t for t in usable if t.get("is_default")), None) or (usable[0] if usable else None)
        return default["id"] if default else None
    found = next((t for t in usable if str(t["id"]) == str(ref).strip() or t["name"].lower() == str(ref).strip().lower()), None)
    if not found:
        raise Fail(f"there is no {kind} template {ref!r}. Templates: " + ", ".join(f"{t['name']} (id {t['id']})" for t in usable))
    return found["id"]


def receivers(ids):
    if not ids:
        return 0
    return paged("/subscribers", {"list_id": ids, "per_page": 1, "query": RECEIVERS})[1]


def subscriber_by_email(email):
    q = "subscribers.email = '" + email.strip().lower().replace("'", "''") + "'"
    found = paged("/subscribers", {"query": q, "per_page": 1})[0]
    return found[0] if found else None


def like(text):
    return "'%" + text.strip().lower().replace("\\", "\\\\").replace("'", "''").replace("%", "\\%").replace("_", "\\_") + "%'"


# Contact tags live in the contact's attributes, as "Group/Name" (or just "Name"):
#   attribs.tags = ["Berlin/Mitte", "VIP"]   — searchable as subscribers.attribs->'tags' @> '["…"]'
TAGS = "tags"
HAS_TAGS = f"jsonb_typeof(subscribers.attribs->'{TAGS}') = 'array'"
TAG_LIST_TAG = "contact-tag"    # the tag fill_tag_list puts on the lists it fills


def tags_of(s):
    t = (s.get("attribs") or {}).get(TAGS)
    return [x.strip() for x in t if isinstance(x, str) and x.strip()] if isinstance(t, list) else []


def split_tag(tag):
    city, _, region = tag.partition("/")
    return (city.strip(), region.strip()) if region else ("", city.strip())


def tag_label(tag):
    city, region = split_tag(tag)
    return f"{region} ({city})" if city else region


def tag_query(tag):
    return f"subscribers.attribs->'{TAGS}' @> '" + json.dumps([tag], ensure_ascii=False).replace("'", "''") + "'::jsonb"


def tag_counts():
    counts = {}
    for s in paged("/subscribers", {"query": HAS_TAGS})[0]:
        for t in tags_of(s):
            counts[t] = counts.get(t, 0) + 1
    return counts


def resolve_tag(a, must_exist=False):
    """The tag a call means: tag "Berlin/Mitte", "Mitte (Berlin)", or name + optional group; a bare name
    that exactly one existing tag has is that tag."""
    raw = " ".join(str(a.get("tag") or "").split())
    city, region = " ".join(str(a.get("city") or "").split()), " ".join(str(a.get("region") or "").split())
    m = re.fullmatch(r"(.+?)\s*\((.+)\)", raw)
    if m:
        region, city = m.group(1).strip(), m.group(2).strip()
    elif raw:
        c, r = split_tag(raw)
        city, region = c or city, r
    if not region:
        raise Fail("say which tag: tag \"Berlin/Mitte\", or region (and city)")
    if "/" in region or "/" in city:
        raise Fail("a city or region name cannot contain /")
    existing = tag_counts()
    if city:
        tag = f"{city}/{region}"
        same = next((t for t in existing if t.casefold() == tag.casefold()), None)
        tag = same or tag
    else:
        same = [t for t in existing if split_tag(t)[1].casefold() == region.casefold()]
        if len(same) > 1:
            raise Fail(f"{region!r} is a region in more than one city: " + ", ".join(tag_label(t) for t in same) + "; say the city too")
        tag = same[0] if same else region
    if must_exist and tag not in existing:
        raise Fail(f"no contact has the tag {tag_label(tag)!r}. Tags: " + (", ".join(tag_label(t) for t in sorted(existing)) or "none yet"))
    return tag


def save_contact(s, attribs, **changes):
    """PUT a contact keeping ALL its lists (unsubscribed ones too, so listmonk keeps the fact that they unsubscribed) and their statuses."""
    body = {"email": s["email"], "name": s["name"], "status": s["status"], "lists": [l["id"] for l in s.get("lists") or []],
            "attribs": attribs, "preconfirm_subscriptions": False, **changes}
    return api("PUT", f"/subscribers/{s['id']}", body)


def campaign_has_tracking(c):
    return bool(re.search(r"\{\{\s*TrackView\s*\}\}", c.get("body") or ""))


def strip_html(html):
    html = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</h[1-6]>|</li>|</tr>|</div>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def inside_roots(path):
    full = os.path.realpath(os.path.expanduser(path))
    if not any(full == r or full.startswith(r + os.sep) for r in ROOTS):
        raise Fail(f"{path} is outside the folders this tool may read ({', '.join(ROOTS)}); give the file's full path in the project")
    return full


def lists_text(lists):
    return ", ".join(f"{l['name']}" for l in lists or []) or "no lists"


campaign_lists_text = lambda c: lists_text(c.get("lists"))


def campaign_line(c):
    when = f", sends at {c['send_at']}" if c.get("send_at") and c["status"] == "scheduled" else ""
    sent = f", sent {c.get('sent', 0)} of {c.get('to_send', 0)}" if c["status"] in ("running", "finished", "paused", "cancelled") else ""
    track = " · open tracking" if campaign_has_tracking(c) else ""
    return f"#{c['id']} {c['name']} [{c['status']}] subject «{c['subject']}» to {campaign_lists_text(c)}{when}{sent}{track}"


# ---------------------------------------------------------------- the manage half
def t_overview(_a):
    counts = api("GET", "/dashboard/counts") or {}
    subs = counts.get("subscribers") or {}
    lists = all_lists()
    recent = paged("/campaigns", {"per_page": 8, "order_by": "created_at", "order": "desc"})[0]
    imp = api("GET", "/import/subscribers") or {}
    out = [f"listmonk at {URL}: {subs.get('total', 0)} contacts ({subs.get('blocklisted', 0)} blocklisted), {len(lists)} lists, "
           f"{(counts.get('campaigns') or {}).get('total', 0)} campaigns, {counts.get('messages', 0)} emails sent so far.",
           "Lists:"] + [
               f"- {l['name']} (id {l['id']}, {l['optin']} opt-in, {l['type']}): {l.get('subscriber_count', 0)} contacts"
               + (f", tags {', '.join(l['tags'])}" if l.get("tags") else "")
               for l in sorted(lists, key=lambda l: l["name"].casefold())]
    out += ["Recent campaigns:"] + ([f"- {campaign_line(c)}" for c in recent] or ["- none"])
    if imp.get("status") not in (None, "none"):
        out.append(f"Last import: {imp.get('name')} {imp.get('status')}, {imp.get('imported', 0)} of {imp.get('total', 0)}")
    return "\n".join(out)


def t_create_list(a):
    name = (a.get("name") or "").strip()
    if not name:
        raise Fail("give the list a name")
    if any(l["name"].lower() == name.lower() for l in all_lists()):
        raise Fail(f"a list called {name!r} exists already")
    tags = [str(t) for t in a.get("tags") or []]
    made = api("POST", "/lists", {"name": name, "type": a.get("type") or "private", "optin": a.get("optin") or "single",
                                  "status": "active", "tags": tags, "description": a.get("description") or ""})
    return f"Made list {made['name']} (id {made['id']}, {made['optin']} opt-in, {made['type']})."


def t_find_contacts(a):
    query = []
    if a.get("search"):
        q = like(str(a["search"]))
        query.append(f"(LOWER(subscribers.email) LIKE {q} OR LOWER(subscribers.name) LIKE {q})")
    if a.get("status") in ("enabled", "disabled", "blocklisted"):
        query.append(f"subscribers.status = '{a['status']}'")
    if a.get("tag") or a.get("region"):
        query.append(tag_query(resolve_tag(a, must_exist=True)))
    params = {"per_page": min(int(a.get("limit") or 50), 200), "page": max(1, int(a.get("page") or 1)), "order_by": "id", "order": "desc"}
    if query:
        params["query"] = " AND ".join(query)
    if a.get("list"):
        params["list_id"] = list_ids(a["list"])
    if a.get("subscription_status") in ("confirmed", "unconfirmed", "unsubscribed"):
        params["subscription_status"] = a["subscription_status"]
    data = api("GET", "/subscribers", query=params) or {}
    rows = data.get("results") or []
    lines = [f"{data.get('total', 0)} contacts match" + (f"; page {params['page']}, {len(rows)} shown" if data.get("total", 0) > len(rows) else "") + ":"]
    for s in rows:
        subs = ", ".join(f"{l['name']}{'' if l.get('subscription_status') == 'confirmed' else ' (' + str(l.get('subscription_status')) + ')'}" for l in s.get("lists") or [])
        attribs = json.dumps(s.get("attribs"), ensure_ascii=False) if s.get("attribs") else ""
        lines.append(f"- #{s['id']} {s['name']} <{s['email']}> [{s['status']}] lists: {subs or 'none'}" + (f" attribs {attribs}" if attribs else ""))
    return "\n".join(lines)


def t_add_contact(a):
    email = (a.get("email") or "").strip().lower()
    if not EMAIL.match(email):
        raise Fail("give a valid email address")
    if subscriber_by_email(email):
        raise Fail(f"{email} is already a contact; use update_contact or change_lists")
    made = api("POST", "/subscribers", {"email": email, "name": (a.get("name") or email.split("@")[0]).strip(), "status": "enabled",
                                        "lists": list_ids(a.get("lists")), "attribs": a.get("attribs") or {},
                                        "preconfirm_subscriptions": yes(a.get("confirmed"), True)})
    return f"Added #{made['id']} {made['name']} <{made['email']}> to {lists_text(made.get('lists'))}."


def t_update_contact(a):
    s = subscriber_by_email(a.get("email") or "")
    if not s:
        raise Fail(f"no contact has the address {a.get('email')!r}")
    attribs = dict(s.get("attribs") or {})
    attribs.update(a.get("attribs") or {})
    status = a.get("status") or s["status"]
    if status not in ("enabled", "disabled", "blocklisted"):
        raise Fail("status is enabled, disabled or blocklisted")
    made = save_contact(s, attribs, email=(a.get("new_email") or s["email"]).strip().lower(), name=a.get("name") or s["name"], status=status)
    return f"Updated #{made['id']} {made['name']} <{made['email']}> [{made['status']}]."


def t_change_lists(a):
    action = a.get("action")
    if action not in ("add", "remove", "unsubscribe"):
        raise Fail("action is add, remove or unsubscribe")
    emails = a.get("emails") or []
    if not emails or not isinstance(emails, list):
        raise Fail("give the contacts' email addresses")
    ids, missing = [], []
    for e in emails[:1000]:
        s = subscriber_by_email(str(e))
        (ids.append(s["id"]) if s else missing.append(str(e)))
    targets = list_ids(a.get("lists"))
    if not targets:
        raise Fail("say which lists")
    if ids:
        api("PUT", "/subscribers/lists", {"ids": ids, "action": action, "target_list_ids": targets, "status": "confirmed"})
    names = lists_text([l for l in all_lists() if l["id"] in targets])
    verb = {"add": "Added", "remove": "Removed", "unsubscribe": "Unsubscribed"}[action]
    return f"{verb} {len(ids)} contact{'s' if len(ids) != 1 else ''} {'to' if action == 'add' else 'from'} {names}." + (
        f" Not contacts: {', '.join(missing)}." if missing else "")


def t_list_tags(_a):
    tagged = paged("/subscribers", {"query": HAS_TAGS})[0]
    counts = {}
    for c in tagged:
        for t in tags_of(c):
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        return "No contact has a tag yet. A tag is a name, optionally under a group, e.g. Berlin/Mitte; add them with tag_contacts."
    by_city = {}
    for t, n in counts.items():
        by_city.setdefault(split_tag(t)[0], []).append((split_tag(t)[1], n, t))
    out = [f"{len(counts)} tag{'s' if len(counts) != 1 else ''} on {len(tagged)} contact{'s' if len(tagged) != 1 else ''}:"]
    for city in sorted(by_city, key=lambda c: (c == "", c.casefold())):
        out.append(f"{city or 'No group'}:")
        out += [f"- {region}: {n} contact{'s' if n != 1 else ''} (tag {t})" for region, n, t in sorted(by_city[city], key=lambda x: x[0].casefold())]
    return "\n".join(out)


def t_tag_contacts(a):
    """Adds or removes one tag for the contacts given by email, or for everyone in lists or with another tag."""
    action = a.get("action") or "add"
    if action not in ("add", "remove"):
        raise Fail("action is add or remove")
    tag = resolve_tag(a, must_exist=action == "remove")
    targets, missing = [], []
    if a.get("emails"):
        if not isinstance(a["emails"], list):
            raise Fail("emails is a list of addresses")
        for e in a["emails"][:5000]:
            s = subscriber_by_email(str(e))
            (targets.append(s) if s else missing.append(str(e)))
    elif a.get("lists") or a.get("with_tag"):
        query = {}
        if a.get("lists"):
            query["list_id"] = list_ids(a["lists"])
        if a.get("with_tag"):
            query["query"] = tag_query(resolve_tag({"tag": a["with_tag"]}, must_exist=True))
        targets = paged("/subscribers", query)[0]
    else:
        raise Fail("say who: emails, or lists and/or with_tag (everyone in them)")
    if len(targets) > 5000:
        raise Fail(f"that is {len(targets)} contacts; do it in parts of at most 5000 (e.g. per list)")
    changed = 0
    for s in targets:
        cur = tags_of(s)
        new = sorted(set(cur) | {tag}) if action == "add" else [t for t in cur if t != tag]
        if new == sorted(cur) and (action == "remove" or tag in cur):
            continue
        attribs = dict(s.get("attribs") or {})
        if new:
            attribs[TAGS] = new
        else:
            attribs.pop(TAGS, None)
        save_contact(s, attribs)
        changed += 1
    verb = "Added the tag" if action == "add" else "Removed the tag"
    return (f"{verb} {tag_label(tag)} (stored as {tag!r}) {'to' if action == 'add' else 'from'} {changed} contact{'s' if changed != 1 else ''}"
            + (f"; {len(targets) - changed} already {'had' if action == 'add' else 'did not have'} it" if len(targets) != changed else "")
            + ". Their lists and subscriptions are unchanged." + (f" Not contacts: {', '.join(missing)}." if missing else ""))


def t_fill_tag_list(a):
    """Campaigns go to lists: fills the list "Name (Group)" with exactly the contacts that have the tag now."""
    tag = resolve_tag(a, must_exist=True)
    name = tag_label(tag)
    lists = all_lists()
    target = next((l for l in lists if l["name"] == name and TAG_LIST_TAG in (l.get("tags") or [])), None)
    if not target:
        target = api("POST", "/lists", {"name": name, "type": "private", "optin": "single", "status": "active",
                                        "tags": [TAG_LIST_TAG], "description": f"People with the tag {tag}."})
    now = [s["id"] for s in paged("/subscribers", {"list_id": [target["id"]]})[0]]
    tagged = [s["id"] for s in paged("/subscribers", {"query": tag_query(tag)})[0]]
    gone = sorted(set(now) - set(tagged))
    if gone:
        api("PUT", "/subscribers/lists", {"ids": gone, "action": "remove", "target_list_ids": [target["id"]]})
    if tagged:
        api("PUT", "/subscribers/lists", {"ids": tagged, "action": "add", "target_list_ids": [target["id"]], "status": "confirmed"})
    return (f"The list {name!r} (id {target['id']}) now holds the {len(tagged)} contacts tagged "
            f"{name}" + (f" ({len(gone)} who no longer have the tag were taken out)" if gone else "")
            + f". Use it in create_campaign as lists [{target['id']}]. Run fill_tag_list again before a later campaign, as tags change.")


def t_import_contacts(a):
    if a.get("path") and a.get("rows"):
        raise Fail("give either path or rows, not both")
    if a.get("path"):
        full = inside_roots(a["path"])
        if not os.path.isfile(full) or os.path.getsize(full) > MAX_FILE:
            raise Fail(f"{a['path']} is not a file of at most 20 MB")
        data, name = open(full, "rb").read(), os.path.basename(full)
        head = data[:4096].decode("utf-8-sig", "replace").splitlines()[0] if data else ""
        delim = a.get("delimiter") or (";" if head.count(";") > head.count(",") else ",")
        check_csv(data, delim)
    else:
        rows = a.get("rows") or []
        if not rows or not isinstance(rows, list):
            raise Fail("give path (a CSV file) or rows (a list of {email, name, attribs})")
        if len(rows) > MAX_ROWS:
            raise Fail(f"at most {MAX_ROWS} rows at once; put more in a CSV file")
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(["email", "name", "attributes"])
        for r in rows:
            if not isinstance(r, dict) or not EMAIL.match(str(r.get("email", "")).strip()):
                raise Fail(f"each row needs a valid email: {r!r}")
            out.writerow([str(r["email"]).strip().lower(), str(r.get("name") or "").strip(), json.dumps(r.get("attribs") or {}, ensure_ascii=False)])
        data, name, delim = buf.getvalue().encode(), "contacts.csv", ","
    targets = list_ids(a.get("lists"))
    if not targets and a.get("mode", "subscribe") == "subscribe":
        raise Fail("say which lists the contacts join")
    state, stalled = import_state()
    if running(state):
        what = f"another import is running ({state.get('name')}, {state.get('imported', 0)} of {state.get('total', 0)})"
        if stalled >= STALL_S:
            raise Fail(f"{what} and has made no progress for {minutes(stalled)}: it is stuck, not slow. Do not wait for it: call stop_import, then import again.")
        raise Fail(f"{what}. Call import_status in a minute to see it move, rather than sleeping; start this one when it has finished.")
    overwrite = yes(a.get("overwrite"))
    params = {"mode": a.get("mode") or "subscribe", "subscription_status": "confirmed" if yes(a.get("confirmed"), True) else "unconfirmed",
              "delim": delim, "lists": targets, "overwrite": overwrite, "overwrite_userinfo": overwrite, "overwrite_subscription_status": overwrite}
    api("POST", "/import/subscribers", form={"params": json.dumps(params), "file": (name, data, "text/csv")})
    SEEN.clear()
    started = time.time()
    while time.time() - started < IMPORT_WAIT_S:          # most imports finish in seconds
        time.sleep(1)
        state, stalled = import_state()
        if not running(state) or stalled >= STALL_S:
            break
    out = (f"Import {state.get('status')}: {state.get('imported', 0)} of {state.get('total', 0)} rows into "
           f"{lists_text([l for l in all_lists() if l['id'] in targets])}" + (", existing contacts updated" if overwrite else ", existing contacts left as they were") + ".")
    if running(state):
        out += "\n" + import_advice(state, stalled)
    return out + log_tail()


IMPORT_COLUMNS = ("email", "name", "attributes")
MAX_SHOWN = 5


def check_csv(data, delim):
    """A contacts file as listmonk's importer needs it, checked before upload: a bad file can leave listmonk's import
    hanging (a file with one column per field did, on 2026-11-13). Columns: email, name and attributes only; every email
    valid; attributes empty or a JSON object."""
    text = data.decode("utf-8-sig", "replace")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows or not any(c.strip() for c in rows[0]):
        raise Fail("the file is empty; its first line must name the columns: email,name,attributes")
    head = rows[0]
    wanted = [c.strip().strip('"') for c in head]
    problems = []
    if any(c not in IMPORT_COLUMNS for c in wanted) or "email" not in wanted or len(set(wanted)) != len(wanted):
        others = [c for c in wanted if c not in IMPORT_COLUMNS]
        hint = ""
        if any(c.lower() in ("attribs", "attribute", "attrs", "fields") for c in others):
            hint = " The column for extra fields is called attributes, not attribs."
        elif others:
            hint = (f" Put other fields ({', '.join(others[:8])}) inside attributes as one JSON object per row, "
                    'such as {"city": "Αθήνα", "mobile": "6900000000"}; listmonk has no columns of its own for them.')
        raise Fail(f"listmonk imports only the columns email, name and attributes (in that spelling, lower case); this file has "
                   f"{', '.join(repr(c) for c in wanted)}.{hint} Nothing was imported.")
    at = {c: i for i, c in enumerate(wanted)}
    for n, row in enumerate(rows[1:], 2):
        if not any(c.strip() for c in row):
            continue
        if len(row) != len(head):
            problems.append(f"line {n}: {len(row)} cells, the header has {len(head)} (a {delim!r} inside a value needs quotes)")
            continue
        email = row[at["email"]].strip()
        if not EMAIL.match(email):
            problems.append(f"line {n}: {email!r} is not an email address")
        if "attributes" in at and row[at["attributes"]].strip():
            try:
                value = json.loads(row[at["attributes"]])
            except ValueError:
                value = None
            if not isinstance(value, dict):
                problems.append(f"line {n}: attributes must be a JSON object such as {{\"city\": \"Αθήνα\"}}, got {row[at['attributes']][:80]!r}")
        if len(problems) >= 50:
            break
    if problems:
        raise Fail(f"{len(problems)}{'+' if len(problems) >= 50 else ''} rows would fail in listmonk, so nothing was imported. Fix them "
                   f"(or leave those rows out) and import again:\n" + "\n".join("- " + p for p in problems[:MAX_SHOWN])
                   + (f"\n- … and {len(problems) - MAX_SHOWN} more" if len(problems) > MAX_SHOWN else ""))


def running(state):
    return state.get("status") in ("importing", "stopping")


def minutes(seconds):
    return f"{int(seconds)} seconds" if seconds < 120 else f"{int(seconds // 60)} minutes"


def import_state():
    """listmonk's import, and how long it has shown no progress (0 when none runs). SEEN keeps it between calls."""
    state = api("GET", "/import/subscribers") or {}
    key, now = (state.get("name"), state.get("total"), state.get("status"), state.get("imported")), time.time()
    if SEEN.get("key") != key:
        SEEN.update(key=key, since=now)
    return state, (now - SEEN["since"] if running(state) else 0)


def import_advice(state, stalled):
    if stalled >= STALL_S:
        return (f"It has made no progress for {minutes(stalled)}: it is stuck, not slow, and waiting will not help. Call stop_import, "
                "then import again (contacts already imported stay).")
    return "It is still working. Call import_status in a minute to see it move, rather than sleeping."


def log_tail():
    logs = api("GET", "/import/subscribers/logs") or ""
    tail = "\n".join(str(logs).strip().splitlines()[-8:])
    return f"\nLast lines of listmonk's import log:\n{tail}" if tail else ""


def t_import_status(_a):
    state, stalled = import_state()
    if not running(state):
        return (f"No import is running. The last one: {state.get('name') or 'none'}, {state.get('status')}, "
                f"{state.get('imported', 0)} of {state.get('total', 0)} rows." + log_tail())
    return (f"Importing {state.get('name')}: {state.get('imported', 0)} of {state.get('total', 0)} rows, status {state.get('status')}, "
            f"no progress for {minutes(stalled)}.\n" + import_advice(state, stalled) + log_tail())


def t_stop_import(_a):
    state, _ = import_state()
    if not running(state):
        return f"No import is running (the last one, {state.get('name') or 'none'}, is {state.get('status')}). Nothing to stop."
    api("DELETE", "/import/subscribers")
    started = time.time()
    while time.time() - started < STOP_WAIT_S:
        time.sleep(1)
        state, _ = import_state()
        if not running(state):
            return (f"Stopped the import of {state.get('name')} ({state.get('imported', 0)} of {state.get('total', 0)} rows were in; they stay). "
                    "Import again now; existing contacts are skipped unless overwrite is true.")
    return (f"listmonk could not stop it: still {state.get('status')} after {int(STOP_WAIT_S)} seconds, at {state.get('imported', 0)} of {state.get('total', 0)}. "
            "Something inside listmonk holds it, usually a database lock left by an earlier import, and only restarting listmonk clears that. "
            + "Call restart_listmonk with the reason if you have it (the user approves it), then import again; otherwise ask the user to "
            "restart listmonk. Do not keep waiting.")


def t_list_templates(_a):
    templates = api("GET", "/templates", query={"no_body": "true"}) or []
    return "\n".join([f"{len(templates)} templates:"] + [f"- {t['name']} (id {t['id']}, {t['type']}{', default' if t.get('is_default') else ''})" for t in templates])


def t_list_campaigns(a):
    params = {"per_page": min(int(a.get("limit") or 30), 100), "order_by": "created_at", "order": "desc"}
    if a.get("status"):
        params["status"] = a["status"]
    if a.get("search"):
        params["query"] = a["search"]
    rows, total = paged("/campaigns", params)
    return "\n".join([f"{total} campaigns" + (f", newest {len(rows)} shown" if total > len(rows) else "") + ":"] + [f"- {campaign_line(c)}" for c in rows])


def t_get_campaign(a):
    c = campaign_of(a.get("campaign"))
    ids = [l["id"] for l in c.get("lists") or []]
    out = [campaign_line(c), f"From: {c.get('from_email')}", f"Content: {c.get('content_type')}, template id {c.get('template_id')}",
           f"Its lists reach about {receivers(ids)} contacts now (not blocklisted, not unsubscribed).",
           "Open tracking: " + ("on" if campaign_has_tracking(c) else "off")]
    if c["status"] in ("running", "paused", "finished", "cancelled"):
        stats = api("GET", "/campaigns/running/stats", query={"campaign_id": c["id"]}) or []
        if stats:
            s = stats[0]
            out.append(f"Progress: {s.get('sent')} of {s.get('to_send')} sent, {s.get('net_rate', 0)} per minute")
    body = c.get("body") or ""
    out.append("Body (" + str(len(body)) + " characters):\n" + (body[:3000] + ("\n…" if len(body) > 3000 else "")))
    return "\n".join(out)


def campaign_body(a, base=None):
    """The listmonk body for a create/update: what the caller gives, else what the campaign already has (attachments too)."""
    base = base or {}
    content_type = a.get("content_type") or base.get("content_type") or "html"
    if content_type not in ("html", "richtext", "markdown", "plain", "visual"):
        raise Fail("content_type is html, richtext, markdown or plain")
    lists = list_ids(a["lists"]) if a.get("lists") is not None else sorted(l["id"] for l in base.get("lists") or [])
    body = {"name": a.get("name") or base.get("name"), "subject": a.get("subject") or base.get("subject"),
            "lists": lists, "from_email": a.get("from_email") or base.get("from_email") or "", "content_type": content_type,
            "body": a.get("body") if a.get("body") is not None else base.get("body", ""),
            "altbody": a.get("altbody") if a.get("altbody") is not None else base.get("altbody"),
            "template_id": template_id(a["template"]) if a.get("template") else (base.get("template_id") or template_id(None)),
            "tags": [str(t) for t in a["tags"]] if a.get("tags") is not None else list(base.get("tags") or []),
            "messenger": base.get("messenger") or "email", "type": "regular",
            "send_at": a.get("send_at") if a.get("send_at") is not None else base.get("send_at"),
            "headers": base.get("headers") or [],
            # listmonk drops what an update leaves out: attachments go back as they were
            "media": [x["id"] for x in base.get("media") or [] if isinstance(x, dict) and x.get("id")]}
    if not body["name"] or not body["subject"]:
        raise Fail("a campaign needs a name and a subject")
    if not body["lists"]:
        raise Fail("say which lists the campaign goes to (to email everyone with a contact tag, fill_tag_list makes a list of them)")
    if not (body["body"] or "").strip():
        raise Fail("the campaign has no body")
    return body


def t_create_campaign(a):
    made = api("POST", "/campaigns", campaign_body(a))
    reach = receivers([l["id"] for l in made.get("lists") or []])
    return (f"Made draft campaign #{made['id']} {made['name']} to {campaign_lists_text(made)}, reaching about {reach} contacts. "
            "Nothing was sent. Look at it with preview_campaign, then send a test to the user before anyone starts it.")


def t_update_campaign(a):
    c = campaign_of(a.get("campaign"))
    if c["status"] not in ("draft", "paused", "scheduled"):
        raise Fail(f"campaign #{c['id']} is {c['status']}; only a draft, paused or scheduled campaign can change")
    made = api("PUT", f"/campaigns/{c['id']}", campaign_body(a, c))
    return f"Updated campaign #{made['id']} {made['name']} [{made['status']}] to {campaign_lists_text(made)}. Nothing was sent."


def t_copy_campaign(a):
    """The same email again to other lists: body, subject, template, sender, tags and attachments stay; the lists are new."""
    src = campaign_of(a.get("campaign"))
    if a.get("lists") in (None, "", []):
        raise Fail("say which lists the copy goes to. The original's lists are not copied")
    base = {**src, "lists": [], "send_at": None}
    args = {"name": a.get("name") or f"{src['name']} (copy)", "subject": a.get("subject"), "lists": a["lists"], "tags": a.get("tags")}
    made = api("POST", "/campaigns", campaign_body(args, base))
    files = [x.get("filename") or str(x.get("id")) for x in made.get("media") or [] if isinstance(x, dict)]
    return (f"Made draft campaign #{made['id']} {made['name']}, a copy of #{src['id']} (same subject, text, template"
            + (f", attachments {', '.join(files)}" if files else "") + f"), to {campaign_lists_text(made)}, reaching about "
            + f"{receivers([l['id'] for l in made.get('lists') or []])} contacts. Anyone who is also on the original's lists would get the email "
            + "again. Nothing was sent. Look at it with preview_campaign, then send a test to the user before anyone starts it.")


def t_preview_campaign(a):
    c = campaign_of(a.get("campaign"))
    form = urllib.parse.urlencode({"body": c.get("body") or "", "content_type": c.get("content_type"), "template_id": c.get("template_id") or 0})
    req_html = api_preview(c["id"], form)
    out = [f"Campaign #{c['id']} {c['name']}, subject «{c['subject']}», as a contact will see it ({len(req_html)} characters of HTML)."]
    if a.get("save_to"):
        full = inside_roots(a["save_to"])
        if not full.endswith((".html", ".htm")):
            raise Fail("save_to must be an .html file")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(req_html)
        out.append(f"Saved the HTML to {full}; open it in Studio to look at it.")
    text = strip_html(req_html)
    out.append("Its text:\n" + text[:4000] + ("\n…" if len(text) > 4000 else ""))
    return "\n".join(out)


def api_preview(cid, form):
    if not USER or not TOKEN:
        api("GET", "/health")                  # raises the set-up message
    req = urllib.request.Request(f"{URL}/api/campaigns/{cid}/preview", data=form.encode(), method="POST",
                                 headers={"Authorization": "token " + USER + ":" + TOKEN, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise Fail(f"listmonk could not render it (HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]})")
    except (urllib.error.URLError, OSError) as e:
        raise Fail(f"listmonk at {URL} did not answer ({e})")


# ---------------------------------------------------------------- the send half
def t_send_test(a):
    c = campaign_of(a.get("campaign"))
    emails = [str(e).strip().lower() for e in a.get("emails") or []]
    if not emails or len(emails) > 5:
        raise Fail("give 1 to 5 addresses for the test")
    missing = [e for e in emails if not subscriber_by_email(e)]
    if missing:
        raise Fail(f"listmonk sends tests only to existing contacts; not contacts: {', '.join(missing)}. Add them with add_contact first (to no list, or a test list).")
    body = campaign_body({}, c)
    api("POST", f"/campaigns/{c['id']}/test", {**body, "subscribers": emails})
    return f"Sent a test of campaign #{c['id']} {c['name']} to {', '.join(emails)}. Nobody else got it."


def checked_count(c, expected):
    """Compares who the campaign's lists reach now with what the caller expects."""
    now = receivers([l["id"] for l in c.get("lists") or []])
    if not isinstance(expected, int) or isinstance(expected, bool) or expected != now:
        raise Fail(f"campaign #{c['id']} reaches {now} contacts now ({campaign_lists_text(c)}). Call again with "
                   f"recipients: {now} if that is what the user expects.")
    return now


def set_status(c, status):
    """Starts, schedules, pauses or cancels a campaign with listmonk's own status route; returns the campaign."""
    return api("PUT", f"/campaigns/{c['id']}/status", {"status": status})


def t_start_campaign(a):
    c = campaign_of(a.get("campaign"))
    if c["status"] not in ("draft", "paused", "scheduled"):
        raise Fail(f"campaign #{c['id']} is {c['status']}; it cannot start")
    n = checked_count(c, a.get("recipients"))
    if c.get("send_at") and c["status"] == "draft":
        api("PUT", f"/campaigns/{c['id']}", {**campaign_body({}, c), "send_at": None})     # now, not at its old time
    done = set_status(c, "running")
    return (f"Started campaign #{done['id']} {done['name']}: sending to about {n} contacts at listmonk's pace. "
            "pause_campaign stops it; get_campaign shows progress.")


def t_schedule_campaign(a):
    c = campaign_of(a.get("campaign"))
    when = (a.get("send_at") or "").strip()
    if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d", when):
        raise Fail("send_at is a date and time like 2026-11-20T09:00:00+02:00")
    if c["status"] not in ("draft", "paused", "scheduled"):
        raise Fail(f"campaign #{c['id']} is {c['status']}; it cannot be scheduled")
    n = checked_count(c, a.get("recipients"))
    api("PUT", f"/campaigns/{c['id']}", {**campaign_body({}, c), "send_at": when})
    done = set_status(c, "scheduled")
    return f"Scheduled campaign #{done['id']} {done['name']} for {done.get('send_at')}, to about {n} contacts. pause_campaign or cancel_campaign stops it."


def t_pause_campaign(a):
    c = campaign_of(a.get("campaign"))
    done = set_status(c, "paused")
    return f"Paused campaign #{done['id']} {done['name']}; start_campaign carries on where it stopped."


def t_cancel_campaign(a):
    c = campaign_of(a.get("campaign"))
    done = set_status(c, "cancelled")
    return f"Cancelled campaign #{done['id']} {done['name']}; it cannot be started again."


def t_restart_listmonk(a):
    why = str(a.get("reason") or "").strip()
    if not why:
        raise Fail("say why listmonk needs a restart (reason)")
    try:
        done = subprocess.run(shlex.split(RESTART), capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(f"the restart command failed: {e}")
    if done.returncode != 0:
        raise Fail(f"the restart command exited {done.returncode}: {(done.stderr or done.stdout).strip()[-400:]}")
    started = time.time()
    while time.time() - started < 90:
        time.sleep(2)
        try:
            api("GET", "/health", timeout=5)
            break
        except Fail:
            continue
    else:
        raise Fail("listmonk was restarted but has not answered for 90 seconds; tell the user")
    SEEN.clear()
    state, _ = import_state()
    return (f"Restarted listmonk ({why}); it answers again after {int(time.time() - started)} seconds. Import: "
            f"{state.get('name') or 'none'}, {state.get('status')}. An import that was running is gone: import it again. "
            "A campaign that was sending may be paused: check list_campaigns.")


BACKUP = os.environ.get("LISTMONK_BACKUP", "").strip()            # e.g. "docker exec listmonk_db pg_dumpall -U listmonk"
BACKUP_DIR = os.path.expanduser(os.environ.get("LISTMONK_BACKUP_DIR") or "~/listmonk-backups")
DELETE_BATCH = 500


def save_database(why):
    """listmonk's whole database, saved before anything is deleted; the file's path, or "" without LISTMONK_BACKUP."""
    if not BACKUP:
        return ""
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    target = os.path.join(BACKUP_DIR, f"listmonk-before-{why}-{time.strftime('%Y%m%d-%H%M%S')}.sql")
    with open(target, "wb") as fh:
        done = subprocess.run(shlex.split(BACKUP), stdout=fh, stderr=subprocess.PIPE, timeout=900)
    if done.returncode != 0 or os.path.getsize(target) < 1000:
        os.remove(target)
        raise Fail("could not save listmonk's database first, so nothing was deleted: " + done.stderr.decode("utf-8", "replace")[-300:])
    os.chmod(target, 0o600)
    return target


def t_delete_contacts(a):
    """Deletes contacts for good: everyone in the given lists, except anyone also in a list to keep. The count must
    match, as for start_campaign, so the user approves the real number; the database is saved first."""
    targets = list_ids(a.get("lists"))
    if not targets:
        raise Fail("say whose contacts to delete: lists")
    keep = set(list_ids(a.get("keep")))
    if keep & set(targets):
        raise Fail("a list cannot be both deleted and kept")
    found = paged("/subscribers", {"list_id": targets})[0]
    doomed = [x for x in found if not any(l["id"] in keep for l in x.get("lists") or [])]
    kept = len(found) - len(doomed)
    names = lists_text([l for l in all_lists() if l["id"] in targets])
    try:
        count = int(a.get("count"))
    except (TypeError, ValueError):
        count = None
    if count != len(doomed):
        raise Fail(f"this would delete {len(doomed)} contacts (everyone in {names}" + (f", keeping {kept} who are also in a kept list" if kept else "")
                   + f"), not {a.get('count')!r}. If that is what the user wants, call again with count {len(doomed)}, which they see when approving.")
    if not doomed:
        return "Nothing to delete: those lists have no contacts outside the kept lists."
    saved = save_database("delete")
    ids = [x["id"] for x in doomed]
    for i in range(0, len(ids), DELETE_BATCH):
        api("DELETE", "/subscribers", query={"id": ids[i:i + DELETE_BATCH]})
    left = paged("/subscribers", {"list_id": targets, "per_page": 1})[1]
    return (f"Deleted {len(ids)} contacts from {names}" + (f"; kept {kept} who are also in a kept list" if kept else "") + f". {left} remain in those lists. "
            + (f"listmonk's database was saved first in {saved}, so it can be restored." if saved else "There is no backup (LISTMONK_BACKUP is not set)."))


def t_send_email(a):
    to = (a.get("to") or "").strip().lower()
    s = subscriber_by_email(to)
    if not s:
        raise Fail(f"{to!r} is not a contact; listmonk sends single emails only to contacts (add_contact first)")
    if s["status"] == "blocklisted":
        raise Fail(f"{to} is blocklisted")
    subject, message = (a.get("subject") or "").strip(), a.get("message") or ""
    if not subject or not message.strip():
        raise Fail("give a subject and a message")
    tid = template_id(a.get("template"), "tx")
    if not tid:
        raise Fail("listmonk has no transactional template")
    api("POST", "/tx", {"subscriber_email": to, "template_id": tid, "subject": subject, "content_type": "html",
                        "data": {"subject": subject, "message": message}})
    return f"Sent «{subject}» to {s['name']} <{to}>."


S = {"type": "string"}
LISTS = {"type": "array", "items": {"type": "string"}, "description": "List names or ids"}
CAMPAIGN = {"type": "string", "description": "Campaign id or exact name"}


def tool(name, description, props=None, required=()):
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": props or {}, "required": list(required)}}


MANAGE = [
    (tool("overview", "What is in listmonk: contact and email counts, every list with its size, recent campaigns, the last import. Start here."), t_overview),
    (tool("create_list", "Make a mailing list.", {"name": S, "description": S, "optin": {"type": "string", "enum": ["single", "double"], "description": "single (default; double opt-in emails cannot be confirmed in local mode)"},
          "type": {"type": "string", "enum": ["private", "public"], "description": "private (default) or public"},
          "tags": {"type": "array", "items": S, "description": "listmonk list tags (labels on the list)"}}, ["name"]), t_create_list),
    (tool("find_contacts", "Find contacts by name or email text, list, contact tag, status or subscription status. Shows each contact's lists and attributes.",
          {"search": S, "list": LISTS, "tag": {**S, "description": "Contacts with this tag, e.g. \"Berlin/Mitte\" or \"Mitte\""}, "status": {"type": "string", "enum": ["enabled", "disabled", "blocklisted"]},
           "subscription_status": {"type": "string", "enum": ["confirmed", "unconfirmed", "unsubscribed"]}, "limit": {"type": "integer"}, "page": {"type": "integer"}}), t_find_contacts),
    (tool("add_contact", "Add one contact, optionally to lists (confirmed unless confirmed is false).",
          {"email": S, "name": S, "lists": LISTS, "attribs": {"type": "object", "description": "Extra fields, e.g. {\"city\": \"Αθήνα\"}"}, "confirmed": {"type": "boolean"}}, ["email"]), t_add_contact),
    (tool("update_contact", "Change a contact's name, email, attributes (merged) or status (enabled, disabled, blocklisted). Their lists "
          "and subscriptions (unsubscribed ones too) stay as they are. For tags use tag_contacts.",
          {"email": {**S, "description": "Their current address"}, "new_email": S, "name": S, "attribs": {"type": "object"}, "status": S}, ["email"]), t_update_contact),
    (tool("list_tags", "The contact tags in use (a name, optionally under a group, e.g. Berlin/Mitte), grouped, with how many contacts have each."), t_list_tags),
    (tool("tag_contacts", "Add or remove one contact tag (a name, optionally under a group) for contacts given by email, or for everyone in lists "
          "and/or everyone with another tag. Only the contacts' attributes change; their lists and subscriptions stay as they are.",
          {"tag": {**S, "description": "\"Berlin/Mitte\" or \"Mitte (Berlin)\"; or give region and city"},
           "region": S, "city": S, "action": {"type": "string", "enum": ["add", "remove"]},
           "emails": {"type": "array", "items": S}, "lists": {**LISTS, "description": "Everyone in these lists"},
           "with_tag": {**S, "description": "Everyone who has this other tag"}}), t_tag_contacts),
    (tool("fill_tag_list", "Campaigns go to lists, so to email everyone with a contact tag: first fill a list with them. This fills the list \"Name (Group)\" "
          "(made if needed) with exactly the contacts that have the tag now; then use that list in create_campaign. Run it again before a later campaign, as tags change. Nothing is sent.",
          {"tag": S, "region": S, "city": S}), t_fill_tag_list),
    (tool("change_lists", "Add contacts to lists, remove them, or mark them unsubscribed, by email.",
          {"emails": {"type": "array", "items": S}, "action": {"type": "string", "enum": ["add", "remove", "unsubscribe"]}, "lists": LISTS}, ["emails", "action", "lists"]), t_change_lists),
    (tool("import_contacts", "Import many contacts into lists, from a CSV file in the project (full path; first line names the columns: exactly email, name, and "
          "optionally attributes, a JSON object per row for every other field such as city or mobile; the file is checked first) or from rows. Waits for it to finish and reports. Existing contacts are left as they are unless overwrite is true.",
          {"path": S, "rows": {"type": "array", "items": {"type": "object"}, "description": "[{email, name, attribs}] instead of a file"}, "lists": LISTS,
           "mode": {"type": "string", "enum": ["subscribe", "blocklist"]}, "overwrite": {"type": "boolean"}, "confirmed": {"type": "boolean"},
           "delimiter": {"type": "string", "description": "Column separator; found from the first line if left out"}}, ["lists"]), t_import_contacts),
    (tool("import_status", "How listmonk's current import is going: rows done, and whether it has stopped moving. Use it instead of sleeping while an import runs.",
          {}), t_import_status),
    (tool("stop_import", "Stop the running import (contacts already imported stay). Use it when an import is stuck: no progress for 2 minutes."), t_stop_import),
    (tool("list_templates", "The email templates (campaign and transactional), with the default marked."), t_list_templates),
    (tool("list_campaigns", "Campaigns, newest first, with status, lists and progress.", {"status": {"type": "string", "enum": ["draft", "scheduled", "running", "paused", "finished", "cancelled"]}, "search": S, "limit": {"type": "integer"}}), t_list_campaigns),
    (tool("get_campaign", "One campaign in full: lists, how many contacts it reaches now, progress, and its body.", {"campaign": CAMPAIGN}, ["campaign"]), t_get_campaign),
    (tool("create_campaign", "Make a draft campaign to one or more lists. Nothing is sent. body is HTML by default (content_type html, markdown, richtext or plain); the "
          "template wraps it with the logo and footer (the default template unless one is named). Personalise with {{ .Subscriber.Name }} and "
          "{{ .Subscriber.Attribs.city }}. To count opens, put {{ TrackView }} in the body; to count clicks, write a link as {{ TrackLink \"https://example.com\" }} "
          "(both need tracking to be on in listmonk's settings). To reach everyone with a contact tag, fill_tag_list first and use the list it made.",
          {"name": S, "subject": S, "lists": LISTS, "body": S, "content_type": S, "template": S,
           "tags": {"type": "array", "items": S}, "from_email": {**S, "description": "Leave out for listmonk's default sender"}, "altbody": {**S, "description": "Plain-text version"}},
          ["name", "subject", "lists", "body"]), t_create_campaign),
    (tool("update_campaign", "Change a draft, paused or scheduled campaign: any of its fields. Attachments stay. Nothing is sent.",
          {"campaign": CAMPAIGN, "name": S, "subject": S, "lists": LISTS, "body": S, "content_type": S, "template": S, "tags": {"type": "array", "items": S}, "from_email": S, "altbody": S},
          ["campaign"]), t_update_campaign),
    (tool("copy_campaign", "Copy a campaign to other lists as a new draft: the same email again. Subject, text, template, sender, tags and attachments stay; "
          "give the new lists. Anyone on both the original's lists and the new ones would get it twice. Nothing is sent.",
          {"campaign": {**CAMPAIGN, "description": "The campaign to copy"}, "lists": LISTS, "name": S, "subject": {**S, "description": "Only to change it"},
           "tags": {"type": "array", "items": S}},
          ["campaign", "lists"]), t_copy_campaign),
    (tool("preview_campaign", "The campaign as a contact will see it, inside its template: its text, and the HTML saved to save_to (a full path to an .html file in the project) to show the user.",
          {"campaign": CAMPAIGN, "save_to": S}, ["campaign"]), t_preview_campaign),
]
SEND = [
    (tool("send_test", "Send a test of a campaign to 1 to 5 addresses (they must be contacts). Do this, and let the user look, before starting a campaign.",
          {"campaign": CAMPAIGN, "emails": {"type": "array", "items": S}}, ["campaign", "emails"]), t_send_test),
    (tool("start_campaign", "Start sending a campaign to its lists now (or resume a paused one). Real people get it. recipients must be the number "
          "of contacts it reaches (from get_campaign), which the user sees when approving.", {"campaign": CAMPAIGN, "recipients": {"type": "integer"}},
          ["campaign", "recipients"]), t_start_campaign),
    (tool("schedule_campaign", "Schedule a campaign for a date and time (ISO 8601 with time zone). recipients as for start_campaign.",
          {"campaign": CAMPAIGN, "send_at": S, "recipients": {"type": "integer"}}, ["campaign", "send_at", "recipients"]), t_schedule_campaign),
    (tool("pause_campaign", "Pause a running or scheduled campaign.", {"campaign": CAMPAIGN}, ["campaign"]), t_pause_campaign),
    (tool("cancel_campaign", "Cancel a campaign for good; it cannot be started again.", {"campaign": CAMPAIGN}, ["campaign"]), t_cancel_campaign),
    (tool("delete_contacts", "Delete contacts for good (not just from a list): everyone in the given lists, except anyone also in a list "
          "in keep. count must be the exact number it deletes (if it is not, nothing is deleted and the answer gives the real number). Only when the user asked to delete "
          "contacts. The user approves every call, and listmonk's database is saved first when LISTMONK_BACKUP is set.",
          {"lists": LISTS, "keep": {**LISTS, "description": "Lists whose members are kept even if they are also in a deleted list"},
           "count": {"type": "integer", "description": "How many contacts it deletes"}}, ["lists", "count"]), t_delete_contacts),
    (tool("send_email", "Send one email to one contact through a transactional template (subject and message, HTML allowed).",
          {"to": S, "subject": S, "message": S, "template": {**S, "description": "Transactional template name; the first one if left out"}}, ["to", "subject", "message"]), t_send_email),
]
if RESTART:
    SEND.append((tool("restart_listmonk", "Restart listmonk itself, only when stop_import could not stop a stuck import, or listmonk does not answer. "
                      "The user approves every restart. A running import is lost and a sending campaign may pause.",
                      {"reason": {**S, "description": "What is stuck, for the user approving it"}}, ["reason"]), t_restart_listmonk))
TOOLS = dict((t["name"], (t, fn)) for t, fn in (SEND if HALF == "send" else MANAGE))


def answer(msg_id, result=None, error=None):
    reply = {"jsonrpc": "2.0", "id": msg_id}
    reply.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            continue
        if method == "initialize":
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "listmonk-" + HALF, "version": "1.0"}})
        elif method == "ping":
            answer(msg_id, {})
        elif method == "tools/list":
            answer(msg_id, {"tools": [t for t, _ in TOOLS.values()]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            entry = TOOLS.get(params.get("name"))
            if not entry:
                answer(msg_id, error={"code": -32602, "message": f"unknown tool {params.get('name')!r}"})
                continue
            try:
                text, failed = entry[1](params.get("arguments") or {}), False
            except Fail as e:
                text, failed = "Error: " + str(e), True
            except (ValueError, TypeError, KeyError) as e:
                text, failed = f"Error: {type(e).__name__}: {e}", True
            answer(msg_id, {"content": [{"type": "text", "text": text}], "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": f"unknown method {method!r}"})


if __name__ == "__main__":
    main()
