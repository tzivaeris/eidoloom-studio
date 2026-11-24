#!/usr/bin/env python3
"""An MCP server (stdio) for a local listmonk (MailSender): contacts, lists, imports, campaigns and email.

It runs in one of two halves, so Studio can ask about them differently:
  LISTMONK_TOOLS=manage (default)  looks and prepares: lists, contacts, imports, templates, draft campaigns, previews.
                                   Nothing here sends an email.
  LISTMONK_TOOLS=send              sends: a test of a campaign, starting, scheduling, pausing or cancelling one, and a
                                   single email, and (with LISTMONK_RESTART set, e.g. "docker restart listmonk_app")
                                   restarting listmonk when an import is stuck. Give this half the "ask before every call" setting.
Contacts can carry MailSender's tags (regions under a city, "Θεσσαλονίκη/Καλαμαριά", kept in attribs.tags): list_tags,
tag_contacts, find_contacts' tag and fill_tag_list (a list to send a campaign to everyone with a tag).
Second rounds of the same email: check_already_sent and create/update_campaign's continues (MailSender leaves out whoever
already got, or is still to get, the campaigns a campaign continues); sending_capacity reads Gmail's daily limit from
MailSender's pacer, which spreads a campaign over days by itself.
It talks to listmonk's API at LISTMONK_URL (default http://127.0.0.1:9100) as an API user (Users → New → API user
in listmonk), LISTMONK_USER and LISTMONK_TOKEN, and with the same API user to MailSender at MAILSENDER_URL (default
http://127.0.0.1:8090) for the pacer, continuations and every campaign start, schedule, pause or cancel, so MailSender's
checks run. Contacts files are read only inside LISTMONK_ROOTS (colon-separated,
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

URL = os.environ.get("LISTMONK_URL", "http://127.0.0.1:9100").rstrip("/")
# MailSender itself (its web address on this PC): the sending pacer, what each campaign already reached, and campaign
# start/schedule/pause/cancel, which go through it so its checks run (Gmail's daily limit, leaving out who already got
# a campaign). It accepts the same API user.
MAILSENDER = os.environ.get("MAILSENDER_URL", "http://127.0.0.1:8090").rstrip("/")
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
        raise Fail(f"listmonk at {URL} did not answer ({getattr(e, 'reason', e)}). Is MailSender running "
                   "(docker compose ps in ~/Projects/Tools/MailSender)?")
    if raw:
        return payload.decode("utf-8", "replace")
    return json.loads(payload or b"{}").get("data")


def mailsender(method, path, body=None, timeout=90):
    """A call to MailSender itself (not listmonk): (HTTP status, JSON). Its refusals come back as Fail."""
    if not USER or not TOKEN:
        api("GET", "/health")                  # raises the set-up message
    req = urllib.request.Request(MAILSENDER + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": "token " + USER + ":" + TOKEN, "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(text)
        except ValueError:
            data = {"message": text[:300]}
        if e.code == 409 and isinstance(data, dict) and data.get("held"):
            return 409, data
        msg = (data.get("message") if isinstance(data, dict) else None) or text[:300]
        if e.code == 401:
            raise Fail(f"MailSender at {MAILSENDER} did not accept the API user (401). It needs MailSender's helper from 2026-11-20 or later, "
                       "which accepts listmonk API users; ask the user.")
        if e.code == 403:
            raise Fail(f"MailSender refused: {msg} Only MailSender's admins may do this (cancelling a campaign, for one); "
                       "ask the user to do it in MailSender. Pausing is allowed.")
        raise Fail(f"MailSender said: {msg} (HTTP {e.code})")
    except (urllib.error.URLError, OSError) as e:
        raise Fail(f"MailSender at {MAILSENDER} did not answer ({getattr(e, 'reason', e)}). Nothing was changed; ask the user to check that "
                   "MailSender is running (docker compose ps in ~/Projects/Tools/MailSender). Do not go around it.")


def paged(path, query=None):
    data = api("GET", path, query={**(query or {}), "per_page": (query or {}).get("per_page", "all")})
    return (data or {}).get("results") or [], (data or {}).get("total", 0)


def all_lists():
    return paged("/lists", {"order_by": "name", "order": "asc"})[0]


# MailSender groups lists by tags: a category "cat:Γονείς" and, under it, a sub-category (usually a city) "sub:Θεσσαλονίκη".
CAT, SUB = "cat:", "sub:"


def tag_value(l, prefix):
    return next((t[len(prefix):].replace("\u00a0", " ").strip() for t in l.get("tags") or [] if t.startswith(prefix)), "")


def group_tag(prefix, value):
    """listmonk turns spaces in tags into hyphens ("Αγία Σοφία" became "Αγία-Σοφία"), so spaces are written as no-break
    spaces, which it keeps and MailSender shows as spaces; tag_value reads them back as plain spaces."""
    return prefix + " ".join(str(value).split()).replace(" ", "\u00a0")


def place_of(l):
    """ "Γονείς › Θεσσαλονίκη", or just one of them, as MailSender shows it."""
    return " › ".join(filter(None, (tag_value(l, CAT), tag_value(l, SUB))))


def plain_tags(tags):
    return [t for t in tags or [] if not t.startswith((CAT, SUB))]


def group_of(item, lists):
    """The lists a group names: "Γονείς › Θεσσαλονίκη", "› Θεσσαλονίκη" (any category), "cat:Γονείς" or "sub:Θεσσαλονίκη"; None if it is not one."""
    text = str(item).strip()
    low = lambda v: v.strip().casefold()
    if text.lower().startswith(CAT):
        want_cat, want_sub = text[len(CAT):], None
    elif text.lower().startswith(SUB):
        want_cat, want_sub = None, text[len(SUB):]
    elif "›" in text or " > " in text:
        want_cat, want_sub = re.split(r"\s*(?:›|>)\s*", text, maxsplit=1)
        want_cat = want_cat or None
    else:
        return None
    return [l for l in lists if (want_cat is None or low(tag_value(l, CAT)) == low(want_cat)) and (want_sub is None or low(tag_value(l, SUB)) == low(want_sub))]


def list_ids(names):
    """List ids from names, ids or groups (see group_of); an unknown one is an error that names the lists there are."""
    if names in (None, "", []):
        return []
    if not isinstance(names, list):
        names = [names]
    lists, out = all_lists(), []
    for item in names:
        found = next((l for l in lists if str(l["id"]) == str(item).strip() or l["name"].lower() == str(item).strip().lower()), None)
        group = None if found else group_of(item, lists)
        if group:
            out += [l["id"] for l in group]
            continue
        if not found:
            places = sorted({place_of(l) for l in lists if place_of(l)})
            raise Fail(f"there is no list or group {item!r}. Lists: " + (", ".join(f"{l['name']} (id {l['id']})" for l in lists) or "none yet")
                       + (". Groups: " + ", ".join(places) if places else ""))
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


# MailSender's contact tags (regions under cities) live in the contact's attributes, as "City/Region" (or just "Region"):
#   attribs.tags = ["Θεσσαλονίκη/Καλαμαριά", "VIP"]   — searchable as subscribers.attribs->'tags' @> '["…"]'
TAGS = "tags"
HAS_TAGS = f"jsonb_typeof(subscribers.attribs->'{TAGS}') = 'array'"
TAG_CATEGORY = "Ετικέτες"    # the MailSender list category fill_tag_list puts its lists in


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
    """The tag a call means: tag "Θεσσαλονίκη/Καλαμαριά", "Καλαμαριά (Θεσσαλονίκη)", or region + optional city; a bare region
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
        raise Fail("say which tag: tag \"Θεσσαλονίκη/Καλαμαριά\", or region (and city)")
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


# MailSender keeps per-campaign settings as hidden campaign tags (all start with "ms:"); they must survive edits:
#   ms:tracking / ms:clicks / ms:person   open tracking, click tracking, record who (the body then has {{ TrackView }} / @TrackLink)
#   ms:to-tag:<tag>                       recipients by contact tag (spaces as no-break spaces: listmonk hyphenates spaces in tags)
#   ms:to-list:<id>                       the helper list those recipients are sent through; MailSender refills it before sending
#   ms:continues:<id>                     a continuation: leaves out whoever already got (or is still to get) campaign <id>
#   ms:via-list:<id>                      a continuation's own list, whose people MailSender copies into its helper list
MS, TRACK, CLICKS, PERSON, TO_TAG, TO_LIST, HELPER = "ms:", "ms:tracking", "ms:clicks", "ms:person", "ms:to-tag:", "ms:to-list:", "ms:helper"
CONTINUES, VIA_LIST, TO_SUB = "ms:continues:", "ms:via-list:", "ms:to-sub:"     # ms:to-sub:<id>: one person picked by hand in MailSender
STARTED = ("running", "paused", "finished", "cancelled", "scheduled")


def ids_tagged(tags, prefix):
    return [int(t[len(prefix):]) for t in tags or [] if t.startswith(prefix) and t[len(prefix):].isdigit()]


def markers(c):
    tags = c.get("tags") or []
    to = [t[len(TO_TAG):].replace("\u00a0", " ").strip() for t in tags if t.startswith(TO_TAG)]
    helper = next((int(t[len(TO_LIST):]) for t in tags if t.startswith(TO_LIST) and t[len(TO_LIST):].isdigit()), None)
    return {"to_tags": [t for t in to if t], "helper": helper, "tracking": TRACK in tags, "clicks": CLICKS in tags, "person": PERSON in tags,
            "continues": ids_tagged(tags, CONTINUES), "via": ids_tagged(tags, VIA_LIST), "people": ids_tagged(tags, TO_SUB)}


def visible_tags(tags):
    return [t for t in tags or [] if not str(t).startswith(MS)]


def strip_tracking(body):
    return re.sub(r"\s*\{\{\s*TrackView\s*\}\}", "", body or "").replace("@TrackLink", "")


def add_tracking(body, content_type, opens, clicks):
    """As MailSender saves a tracked campaign: the open pixel at the end, and web links marked for click tracking."""
    b = strip_tracking(body)
    if clicks and content_type != "plain":
        b = (re.sub(r"\]\((https?://[^)\s]+)\)", r"](\1@TrackLink)", b) if content_type == "markdown"
             else re.sub(r'href="(https?://[^"]+)"', r'href="\1@TrackLink"', b))
    if opens and content_type != "plain":
        b = b.rstrip() + "\n{{ TrackView }}"
    return b


def tag_recipients_query(tags, people=()):
    """Who a campaign's tags and hand-picked people reach: enabled contacts with any of the tags, or picked, who never unsubscribed from a list."""
    any_of = [tag_query(t) for t in tags] + ([f"subscribers.id IN ({','.join(str(int(i)) for i in people)})"] if people else [])
    return ("(" + (" OR ".join(any_of) or "false") + ") AND subscribers.status = 'enabled' AND subscribers.id NOT IN "
            "(SELECT subscriber_id FROM subscriber_lists WHERE status = 'unsubscribed')")


def make_helper_list(campaign_name):
    made = api("POST", "/lists", {"name": f"Ετικέτες · {campaign_name}", "type": "private", "optin": "single", "status": "active",
                                  "tags": [group_tag(CAT, TAG_CATEGORY), HELPER],
                                  "description": "Recipients of a campaign sent to contact tags; MailSender refills it right before sending. Do not edit."})
    return made["id"]


def refill_tag_recipients(c):
    """Fill the campaign's helper list with exactly the contacts its tags reach now (what MailSender does before sending)."""
    m = markers(c)
    if not (m["to_tags"] or m["people"]) or not m["helper"]:
        return None
    sel = {"all": True, "search": "", "subscription_status": ""}
    api("PUT", "/subscribers/query/lists", {**sel, "query": "", "list_ids": [m["helper"]], "action": "remove", "target_list_ids": [m["helper"]],
                                            "status": "confirmed"})
    api("PUT", "/subscribers/query/lists", {**sel, "query": tag_recipients_query(m["to_tags"], m["people"]), "list_ids": [], "action": "add",
                                            "target_list_ids": [m["helper"]], "status": "confirmed"})
    return paged("/subscribers", {"list_id": [m["helper"]], "per_page": 1})[1]


def campaign_lists_text(c):
    m = markers(c)
    names = lists_text([l for l in c.get("lists") or [] if l["id"] != m["helper"]]) if c.get("lists") else ""
    areas = ", ".join(tag_label(t) for t in m["to_tags"])
    return (names if names != "no lists" else "") + ((" + " if names and names != "no lists" else "") + f"areas {areas}" if areas else "") or "no lists"


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


def campaign_line(c):
    when = f", sends at {c['send_at']}" if c.get("send_at") and c["status"] == "scheduled" else ""
    sent = f", sent {c.get('sent', 0)} of {c.get('to_send', 0)}" if c["status"] in ("running", "finished", "paused", "cancelled") else ""
    m = markers(c)
    track = (" · open tracking" + (" + clicks" if m["clicks"] else "") + (" (who)" if m["person"] else "")) if m["tracking"] else ""
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
           "Lists (with their MailSender group, Category › Sub-category):"] + [
               f"- {l['name']} (id {l['id']}, {l['optin']} opt-in, {l['type']}): {l.get('subscriber_count', 0)} contacts"
               + (f" · {place_of(l)}" if place_of(l) else "") + (f", tags {', '.join(plain_tags(l['tags']))}" if plain_tags(l.get("tags")) else "")
               for l in sorted(lists, key=lambda l: (tag_value(l, CAT).casefold() or "~", tag_value(l, SUB).casefold(), l["name"].casefold()))
               if HELPER not in (l.get("tags") or [])]
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
    tags = plain_tags(str(t) for t in a.get("tags") or [])
    if str(a.get("category") or "").strip():
        tags.append(group_tag(CAT, a["category"]))          # MailSender's list categories
    if str(a.get("subcategory") or "").strip():
        tags.append(group_tag(SUB, a["subcategory"]))       # and sub-categories, usually a city
    made = api("POST", "/lists", {"name": name, "type": a.get("type") or "private", "optin": a.get("optin") or "single",
                                  "status": "active", "tags": tags, "description": a.get("description") or ""})
    return f"Made list {made['name']} (id {made['id']}, {made['optin']} opt-in, {made['type']})" + (f" in {place_of(made)}" if place_of(made) else "") + "."


def t_set_list_group(a):
    """Moves lists to a category and sub-category, as MailSender's "Move to category": a value left out stays, an empty one is removed."""
    if "category" not in a and "subcategory" not in a:
        raise Fail("give category, subcategory or both (an empty value removes it)")
    by_id = {l["id"]: l for l in all_lists()}
    done = []
    for lid in list_ids(a.get("lists")):
        l = by_id[lid]
        cat = str(a["category"]).strip() if "category" in a and a["category"] is not None else tag_value(l, CAT)
        sub = str(a["subcategory"]).strip() if "subcategory" in a and a["subcategory"] is not None else tag_value(l, SUB)
        tags = plain_tags(l.get("tags")) + ([group_tag(CAT, cat)] if cat else []) + ([group_tag(SUB, sub)] if sub else [])
        api("PUT", f"/lists/{lid}", {"name": l["name"], "type": l["type"], "optin": l["optin"], "status": l.get("status") or "active",
                                     "tags": tags, "description": l.get("description") or ""})
        done.append(f"{l['name']} → {' › '.join(filter(None, (cat, sub))) or 'no group'}")
    if not done:
        raise Fail("say which lists")
    return "Moved " + "; ".join(done) + "."


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
        return ("No contact has a tag yet. Tags are regions under a city, e.g. Θεσσαλονίκη/Καλαμαριά; add them with tag_contacts "
                "(or in MailSender: a contact's Ετικέτες, or Subscribers → select → Προσθήκη ετικέτας).")
    by_city = {}
    for t, n in counts.items():
        by_city.setdefault(split_tag(t)[0], []).append((split_tag(t)[1], n, t))
    out = [f"{len(counts)} tag{'s' if len(counts) != 1 else ''} on {len(tagged)} contact{'s' if len(tagged) != 1 else ''}:"]
    for city in sorted(by_city, key=lambda c: (c == "", c.casefold())):
        out.append(f"{city or 'No city'}:")
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
    """Campaigns go to lists: fills the list "Region (City)" (MailSender category Ετικέτες) with exactly the contacts that have the tag now."""
    tag = resolve_tag(a, must_exist=True)
    city, region = split_tag(tag)
    name = tag_label(tag)
    lists = all_lists()
    target = next((l for l in lists if l["name"] == name and tag_value(l, CAT) == TAG_CATEGORY), None)
    if not target:
        target = api("POST", "/lists", {"name": name, "type": "private", "optin": "single", "status": "active",
                                        "tags": [group_tag(CAT, TAG_CATEGORY)],
                                        "description": f"People with the tag {tag} (filled by MailSender)."})
    now = [s["id"] for s in paged("/subscribers", {"list_id": [target["id"]]})[0]]
    tagged = [s["id"] for s in paged("/subscribers", {"query": tag_query(tag)})[0]]
    gone = sorted(set(now) - set(tagged))
    if gone:
        api("PUT", "/subscribers/lists", {"ids": gone, "action": "remove", "target_list_ids": [target["id"]]})
    if tagged:
        api("PUT", "/subscribers/lists", {"ids": tagged, "action": "add", "target_list_ids": [target["id"]], "status": "confirmed"})
    return (f"The list {name!r} (id {target['id']}, in {TAG_CATEGORY}) now holds the {len(tagged)} contacts tagged "
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
    m = markers(c)
    ids = [l["id"] for l in c.get("lists") or [] if l["id"] != m["helper"]]
    out = [campaign_line(c), f"From: {c.get('from_email')}", f"Content: {c.get('content_type')}, template id {c.get('template_id')}",
           f"Its lists reach about {receivers(ids)} contacts now (not blocklisted, not unsubscribed)."]
    if m["people"]:
        out.append(f"Also {len(m['people'])} people picked by hand in MailSender, filled into its helper list right before it is sent.")
    if m["to_tags"]:
        reach = paged("/subscribers", {"query": tag_recipients_query(m["to_tags"]), "per_page": 1})[1]
        out.append(f"Areas (contact tags): {', '.join(tag_label(t) for t in m['to_tags'])}: {reach} contacts now; filled into its helper list "
                   "right before it is sent (people in a list as well get it once).")
    out.append("Open tracking: " + (("on" + (", clicks too" if m["clicks"] else "") + (", recording who" if m["person"] else ", anonymous"))
                                    if m["tracking"] else "off"))
    if c["status"] in ("draft", "paused", "scheduled"):
        more = continuation_text(c)
        if more:
            out.append(more)
    if c["status"] in ("running", "paused", "finished", "cancelled"):
        stats = api("GET", "/campaigns/running/stats", query={"campaign_id": c["id"]}) or []
        if stats:
            s = stats[0]
            out.append(f"Progress: {s.get('sent')} of {s.get('to_send')} sent, {s.get('net_rate', 0)} per minute")
    body = c.get("body") or ""
    out.append("Body (" + str(len(body)) + " characters):\n" + (body[:3000] + ("\n…" if len(body) > 3000 else "")))
    return "\n".join(out)


CAMPAIGN_FIELDS = ("name", "subject", "lists", "body", "content_type", "template", "tags", "from_email", "altbody", "send_at", "to_tags")


def campaign_body(a, base=None, helper=None):
    """The listmonk body for a create/update. MailSender's hidden settings (ms: tags), its helper list for areas and the tracking
    pixel are kept: tags given by the caller replace only the visible ones; a new body of a tracked campaign gets the pixel back."""
    base = base or {}
    m = markers(base)
    content_type = a.get("content_type") or base.get("content_type") or "html"
    if content_type not in ("html", "richtext", "markdown", "plain", "visual"):
        raise Fail("content_type is html, richtext, markdown or plain")
    to_tags = [resolve_tag({"tag": t}, must_exist=True) for t in a["to_tags"]] if a.get("to_tags") is not None else m["to_tags"]
    helper = helper or m["helper"]
    continues = a["continues"] if a.get("continues") is not None else m["continues"]
    hidden = [t for t in base.get("tags") or [] if str(t).startswith(MS) and not str(t).startswith((TO_TAG, TO_LIST, CONTINUES, VIA_LIST))]
    if to_tags or m["people"]:               # hand-picked people (ms:to-sub, kept above) go through the helper list too
        hidden += [TO_TAG + t.replace(" ", "\u00a0") for t in to_tags] + ([TO_LIST + str(helper)] if helper else [])
    elif continues and helper and helper != -1:
        hidden.append(TO_LIST + str(helper))   # a continuation to lists keeps its helper list (MailSender sends through it, leaving people out)
    hidden += [CONTINUES + str(i) for i in continues]
    shown = visible_tags(a["tags"]) if a.get("tags") is not None else visible_tags(base.get("tags"))
    # The lists as the user chose them: a continuation's own lists were moved into its helper list by MailSender (ms:via-list).
    lists = list_ids(a["lists"]) if a.get("lists") is not None else sorted(
        {l["id"] for l in base.get("lists") or [] if l["id"] != m["helper"]} | set(m["via"]))
    if (to_tags or m["people"]) and helper:      # (-1: a stand-in while checking the fields, before a helper list is made)
        lists = sorted(set(lists) | {helper})
    body_text = a.get("body") if a.get("body") is not None else base.get("body", "")
    if a.get("body") is not None and m["tracking"]:
        body_text = add_tracking(body_text, content_type, True, m["clicks"])
    body = {"name": a.get("name") or base.get("name"), "subject": a.get("subject") or base.get("subject"),
            "lists": lists, "from_email": a.get("from_email") or base.get("from_email") or "", "content_type": content_type,
            "body": body_text,
            "altbody": a.get("altbody") if a.get("altbody") is not None else base.get("altbody"),
            "template_id": template_id(a["template"]) if a.get("template") else (base.get("template_id") or template_id(None)),
            "tags": shown + hidden,
            "messenger": base.get("messenger") or "email", "type": "regular",
            "send_at": a.get("send_at") if a.get("send_at") is not None else base.get("send_at"),
            "headers": base.get("headers") or [],
            # listmonk drops what an update leaves out: attachments go back as they were
            "media": [x["id"] for x in base.get("media") or [] if isinstance(x, dict) and x.get("id")]}
    if not body["name"] or not body["subject"]:
        raise Fail("a campaign needs a name and a subject")
    if not body["lists"]:
        raise Fail("say which lists (or to_tags, areas) the campaign goes to")
    if not (strip_tracking(body["body"]) or "").strip():
        raise Fail("the campaign has no body")
    return body


def helper_for(a, base=None):
    """A helper list for the areas, made the first time a campaign gets to_tags."""
    m = markers(base or {})
    if a.get("to_tags") and not m["helper"]:
        return make_helper_list(a.get("name") or (base or {}).get("name") or "καμπάνια")
    return None


# ---------------------------------------------------------------- continuations (MailSender's bounces/sentlog.py)
# A campaign tagged ms:continues:<id> leaves out whoever already got campaign <id> (MailSender keeps a record list of who
# each campaign reached) or is still to get it (a paused or scheduled one). MailSender applies it when the campaign is
# started or scheduled through it, again before the pacer resumes it, and every 20 s while it waits.
def continues_ids(refs, self_id=None):
    """Campaign ids from ids or names, not the campaign itself. A draft or scheduled one counts with whoever it will reach:
    a campaign can continue one prepared just before it (the first round first, then the rest)."""
    if refs is None:
        return None
    out = []
    for ref in refs if isinstance(refs, list) else [refs]:
        c = campaign_of(ref)
        if c["id"] == self_id:
            raise Fail("a campaign cannot continue itself")
        if c["status"] in ("draft", "scheduled"):
            # Who a campaign still to be sent will reach: its lists, and its areas and people once its helper list is filled
            # (as MailSender does right before sending), so they are left out of this one too.
            refill_tag_recipients(c)
        out.append(c["id"])
    return sorted(set(out))


def exclusion(ids):
    """SQL for who already got (or is still to get) these campaigns, from MailSender; None when that is nobody."""
    if not ids:
        return None
    _, data = mailsender("GET", "/pacer/continues/" + ",".join(str(i) for i in ids))
    return data.get("sql")


def audience_lists(c):
    """The lists a campaign goes to as the user chose them: its own lists (not its helper list) and a continuation's lists."""
    m = markers(c)
    return sorted({l["id"] for l in c.get("lists") or [] if l["id"] != m["helper"]} | set(m["via"]))


def reach_counts(lists, tags=(), people=(), excl=None):
    """(how many contacts the lists, areas and hand-picked people reach, how many of them excl names); each person once."""
    def count(cond):
        n = paged("/subscribers", {"list_id": lists, "per_page": 1, "query": RECEIVERS + cond})[1] if lists else 0
        if tags or people:
            q = tag_recipients_query(tags, people)
            if lists:
                q += f" AND subscribers.id NOT IN (SELECT subscriber_id FROM subscriber_lists WHERE list_id IN ({','.join(map(str, lists))}))"
            n += paged("/subscribers", {"query": q + cond, "per_page": 1})[1]
        return n
    return count(""), (count(f" AND {excl}") if excl else 0)


def campaign_reach(c, excl=None):
    m = markers(c)
    return reach_counts(audience_lists(c), m["to_tags"], m["people"], excl)




def base_name(s):
    """A campaign name without "Αντίγραφο:", "(B)", "· …" and accents, to spot copies of the same campaign (as MailSender's editor does)."""
    import unicodedata
    s = "".join(ch for ch in unicodedata.normalize("NFD", s or "") if not unicodedata.combining(ch)).lower()
    s = re.sub(r"^(αντιγραφο:?|copy of)\s*", "", s)
    s = re.sub(r"\s+·\s+.*$", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    return " ".join(s.split())


def similar_reached(c):
    """Earlier campaigns of the last 60 days with the same subject or name (copies), not continued yet, that already reached
    some of this campaign's recipients: [(campaign, how many)]."""
    m = markers(c)
    since = time.time() - 60 * 86400
    subj, base = (c.get("subject") or "").strip().lower(), base_name(c.get("name"))
    out = []
    for o in paged("/campaigns", {"per_page": 100, "order_by": "created_at", "order": "desc", "no_body": "true"})[0]:
        if o["id"] == c["id"] or o["id"] in m["continues"] or o["status"] not in STARTED or o["status"] == "scheduled":
            continue
        when = o.get("started_at") or o.get("created_at") or ""
        try:
            import datetime
            if datetime.datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp() < since:
                continue
        except ValueError:
            pass
        if not ((subj and (o.get("subject") or "").strip().lower() == subj) or (base and base_name(o["name"]) == base)):
            continue
        try:
            excl = exclusion([o["id"]])
        except Fail:
            continue
        if not excl:
            continue
        n = campaign_reach(c, excl)[1]
        if n:
            out.append((o, n))
        if len(out) >= 4:
            break
    return out


def continuation_text(c):
    """What a campaign leaves out, and a warning for copies of it that already reached some of its recipients."""
    m = markers(c)
    out = []
    if m["continues"]:
        names = ", ".join("#" + str(i) for i in m["continues"])
        if c["status"] in ("draft", "scheduled"):
            total, got = campaign_reach(c, exclusion(m["continues"]))
            out.append(f"It continues {names}: {got} of its {total} contacts already got (or are still to get) those and are left out, "
                       f"so it reaches about {total - got}. MailSender checks this again right before sending.")
        else:
            out.append(f"It continues {names}: MailSender leaves out, of those it still has to send to, whoever already got those.")
    warn = similar_reached(c)
    for o, n in warn:
        out.append(f"Warning: {n} of its recipients already got «{o['name']}» (#{o['id']}, {(o.get('started_at') or '')[:10]}), a campaign "
                   f"with the same subject or name: they would get it twice. To leave them out, update_campaign with continues: "
                   f"[{', '.join(str(i) for i in m['continues'] + [o['id']])}].")
    return " ".join(out)


def t_create_campaign(a):
    a = {**a, "continues": continues_ids(a.get("continues"))}
    campaign_body(a, helper=-1)              # check every field first, so a mistake leaves no helper list behind
    made = api("POST", "/campaigns", campaign_body(a, helper=helper_for(a)))
    m = markers(made)
    reach = receivers([l["id"] for l in made.get("lists") or [] if l["id"] != m["helper"]])
    areas = (f" plus {paged('/subscribers', {'query': tag_recipients_query(m['to_tags']), 'per_page': 1})[1]} contacts in the areas "
             f"(filled in right before sending)") if m["to_tags"] else ""
    more = continuation_text(made)
    return (f"Made draft campaign #{made['id']} {made['name']} to {campaign_lists_text(made)}, reaching about {reach} contacts{areas}. "
            + (more + " " if more else "")
            + "Nothing was sent. Look at it with preview_campaign, then send a test to the user before anyone starts it.")


def t_update_campaign(a):
    c = campaign_of(a.get("campaign"))
    if c["status"] not in ("draft", "paused", "scheduled"):
        raise Fail(f"campaign #{c['id']} is {c['status']}; only a draft, paused or scheduled campaign can change")
    a = {**a, "continues": continues_ids(a.get("continues"), c["id"])}
    campaign_body(a, c, helper=markers(c)["helper"] or -1)     # check first, as for create_campaign
    made = api("PUT", f"/campaigns/{c['id']}", campaign_body(a, c, helper=helper_for(a, c)))
    more = continuation_text(made)
    return f"Updated campaign #{made['id']} {made['name']} [{made['status']}] to {campaign_lists_text(made)}. " + (more + " " if more else "") + "Nothing was sent."


def t_copy_campaign(a):
    """The same email again to other recipients, as MailSender's "Αντίγραφο": body, subject, template, sender, attachments and
    tracking settings stay; recipients are new; by default it continues the original and whatever that continued."""
    src = campaign_of(a.get("campaign"))
    m = markers(src)
    if a.get("lists") is None and a.get("to_tags") is None:
        raise Fail("say who the copy goes to: lists and/or to_tags (areas). The original's recipients are not copied")
    keep = [t for t in src.get("tags") or [] if t in (TRACK, CLICKS, PERSON)]
    base = {**src, "lists": [], "tags": visible_tags(src.get("tags")) + keep, "send_at": None}
    default = [src["id"]] + m["continues"] if src["status"] in STARTED else m["continues"]
    args = {"name": a.get("name") or f"{src['name']} · συνέχεια", "subject": a.get("subject"), "lists": a.get("lists") or [],
            "to_tags": a.get("to_tags") or [], "tags": a.get("tags"),
            "continues": continues_ids(a["continues"]) if a.get("continues") is not None else sorted(set(default))}
    campaign_body(args, base, helper=-1)     # check every field first, so a mistake leaves no helper list behind
    made = api("POST", "/campaigns", campaign_body(args, base, helper=helper_for(args)))
    total, got = campaign_reach(made, exclusion(markers(made)["continues"]))
    files = [x.get("filename") or str(x.get("id")) for x in made.get("media") or [] if isinstance(x, dict)]
    mk = markers(made)
    return (f"Made draft campaign #{made['id']} {made['name']}, a copy of #{src['id']} (same subject, text, template"
            + (f", attachments {', '.join(files)}" if files else "") + (", open tracking" if mk["tracking"] else "") + "), to "
            + f"{campaign_lists_text(made)}. Of its {total} contacts, {got} already got (or are still to get) "
            + (", ".join("#" + str(i) for i in mk["continues"]) or "earlier campaigns") + f" and are left out: it reaches about {total - got}. "
            + "Nothing was sent. Look at it with preview_campaign, then send a test to the user before anyone starts it.")


def t_check_already_sent(a):
    ids = continues_ids(a.get("campaigns"))
    if not ids:
        raise Fail("give the earlier campaigns (ids or names) in campaigns")
    excl = exclusion(ids)
    out = []
    for i in ids:
        c = api("GET", f"/campaigns/{i}")
        out.append(f"- #{c['id']} {c['name']} [{c['status']}]: sent {c.get('sent', 0)} of {c.get('to_send', 0)}")
    if not excl:
        return "\n".join(["Nobody got these campaigns yet, so nobody would be left out:"] + out)
    if a.get("campaign"):
        c = campaign_of(a["campaign"])
        lists, tags, who = audience_lists(c), markers(c)["to_tags"], f"campaign #{c['id']} {c['name']}"
    else:
        lists, tags = list_ids(a.get("lists")), [resolve_tag({"tag": t}, must_exist=True) for t in a.get("to_tags") or []]
        who = ", ".join(filter(None, [lists_text([l for l in all_lists() if l["id"] in lists]) if lists else "",
                                      ", ".join(tag_label(t) for t in tags)]))
    if not lists and not tags and not (a.get("campaign") and markers(c)["people"]):
        return "\n".join(["The earlier campaigns:"] + out + ["Give lists, to_tags or a campaign to count who among them already got these."])
    people = markers(c)["people"] if a.get("campaign") else []
    total, got = reach_counts(lists, tags, people, excl)
    return "\n".join(["The earlier campaigns:"] + out + [
        f"Of the {total} contacts {who} reaches, {got} already got (or are still to get) them, and {total - got} have not.",
        "A campaign made with continues: [" + ", ".join(map(str, ids)) + "] (create_campaign or update_campaign) goes only to those "
        f"{total - got}; MailSender checks again right before sending, so people reached meanwhile are left out too."])


def local_time(iso):
    import datetime
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%a %d/%m %H:%M")
    except (ValueError, AttributeError):
        return str(iso)


def t_sending_capacity(a):
    import datetime
    _, p = mailsender("GET", "/pacer/status")
    now = datetime.datetime.now(datetime.timezone.utc)
    parse = lambda x: datetime.datetime.fromisoformat(x.replace("Z", "+00:00")) if x else None
    blocked = parse(p.get("blocked_until"))
    blocked = blocked if blocked and blocked > now else None
    cap = max(1, int(p["limit"]) - int(p.get("margin") or 0))
    out = [f"Gmail's limit as set in MailSender: {p['limit']} emails in any {p['window_hours']} hours (Gmail counts 24, rolling). "
           f"Sent in that window: {p['used']}. Room now: {0 if blocked else p['left']}."]
    if blocked:
        out.append(f"Nothing may go out until {local_time(p['blocked_until'])} ({p.get('block_reason') or 'Gmail refused to send'}); "
                   "waiting campaigns carry on by themselves then.")
    rows = [c for c in paged("/campaigns", {"per_page": "all", "no_body": "true"})[0] if c["status"] in ("running", "paused", "scheduled")]
    queued = sum(max(0, int(c.get("to_send") or 0) - int(c.get("sent") or 0)) for c in rows)
    waiting = {q["campaign_id"]: q for q in p.get("queue") or []}
    if rows:
        out.append("Still to send:")
        for c in rows:
            left = max(0, int(c.get("to_send") or 0) - int(c.get("sent") or 0))
            q = waiting.get(c["id"])
            note = (" (waiting for room; MailSender resumes it)" if q else "") + (f", starts {local_time(c['send_at'])}" if c["status"] == "scheduled" and c.get("send_at") else "")
            count = f"{left} emails left" if c["status"] != "scheduled" or c.get("to_send") else f"about {campaign_reach(c)[0]} emails"
            out.append(f"- #{c['id']} {c['name']} [{c['status']}]: {count}{note}")
    frees = p.get("next_free") or []
    if frees:
        out.append("Room frees up as earlier emails leave the window: " + ", ".join(f"{f['n']} at {local_time(f['at'])}" for f in frees[:6]) + ".")
    extra = a.get("emails")
    if extra is not None:
        try:
            extra = int(extra)
        except (TypeError, ValueError):
            raise Fail("emails is a number")
        total = queued + extra
        start = blocked or now
        schedule = [(parse(f["at"]), int(f["n"])) for f in p.get("frees") or frees]
        room = cap - max(0, int(p["used"]) - sum(n for at, n in schedule if at <= start))
        end = start if total <= room else None
        for at, n in schedule:               # room grows as earlier emails leave the window
            if end or at <= start:
                continue
            room += n
            if total <= room:
                end = at
        if end is None:                      # more than the window holds: what goes out now leaves it a window later, and so on
            windows = -(-(total - room) // cap)
            end = (schedule[-1][0] if schedule else start) + datetime.timedelta(hours=int(p["window_hours"]) * windows)
        out.append(f"{extra} more emails after the {queued} already waiting: " + (
            f"they fit {'when the block ends, ' + local_time(p['blocked_until']) if blocked else 'now'}." if end == start else
            f"the last of them could go out around {end.astimezone().strftime('%a %d/%m %H:%M')}, as room frees up (an estimate: "
            "it assumes nothing else is sent meanwhile)."))
    out.append("MailSender's pacer pauses campaigns just before the limit and resumes them as room frees up, so one campaign "
               "can be spread over several days by itself: there is no need to split recipients into batches for the limit.")
    return "\n".join(out)


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
    """Refills a campaign's areas (as MailSender does before sending), then compares who it reaches with what the caller expects."""
    if markers(c)["to_tags"] or markers(c)["people"]:
        refill_tag_recipients(c)
    if markers(c)["continues"] and c["status"] != "paused":
        total, got = campaign_reach(c, exclusion(markers(c)["continues"]))
        now = total - got
    else:
        now = receivers([l["id"] for l in c.get("lists") or []])
    if not isinstance(expected, int) or isinstance(expected, bool) or expected != now:
        raise Fail(f"campaign #{c['id']} reaches {now} contacts now ({campaign_lists_text(c)}). Call again with "
                   f"recipients: {now} if that is what the user expects.")
    return now


def tracking_off_for(c):
    """A campaign set to track opens would not be counted while MailSender's tracking connection is off."""
    if not markers(c)["tracking"]:
        return False
    try:
        return bool((api("GET", "/settings") or {}).get("privacy.disable_tracking"))
    except Fail:
        return False          # the API user may not read settings; MailSender itself still blocks such a send


def set_status(c, status):
    """Starts, schedules, pauses or cancels through MailSender, so its checks run; (campaign, None) or (None, pacer) when it held it."""
    code, data = mailsender("PUT", f"/api/campaigns/{c['id']}/status", {"status": status})
    if code == 409:
        return None, data.get("pacer") or {}
    return (data or {}).get("data") or data, None


def held_text(c, pacer):
    when = pacer.get("resume_at") or pacer.get("blocked_until") or ((pacer.get("next_free") or [{}])[0].get("at"))
    return (f"MailSender did not start #{c['id']} {c['name']} now: Gmail's daily limit has no room" + (" (sending is blocked)" if pacer.get("blocked_until") else "")
            + f". It is waiting, and the pacer starts it by itself{(' around ' + local_time(when)) if when else ' when there is room'}. Nothing more to do.")


def t_start_campaign(a):
    c = campaign_of(a.get("campaign"))
    if c["status"] not in ("draft", "paused", "scheduled"):
        raise Fail(f"campaign #{c['id']} is {c['status']}; it cannot start")
    if c["status"] != "paused" and tracking_off_for(c):
        raise Fail(f"campaign #{c['id']} is set to track opens, but MailSender's tracking connection is off, so no open would be counted. "
                   "Ask the user to connect it first (MailSender → the campaign's Open tracking box → Connect now, or turn tracking off "
                   "for this campaign), then start it.")
    n = checked_count(c, a.get("recipients"))
    if c.get("send_at") and c["status"] == "draft":
        api("PUT", f"/campaigns/{c['id']}", {**campaign_body({}, c), "send_at": None})     # now, not at its old time
    done, held = set_status(c, "running")
    if held is not None:
        return held_text(c, held)
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
    done, _ = set_status(c, "scheduled")
    m = markers(c)
    return (f"Scheduled campaign #{done['id']} {done['name']} for {done.get('send_at')}, to about {n} contacts. pause_campaign or cancel_campaign stops it."
            + (" Its areas are filled in again by MailSender in the minutes before it starts." if m["to_tags"] else "")
            + (" It tracks opens: MailSender's tracking connection must be on at that time." if m["tracking"] else "")
            + (" As a continuation, MailSender leaves out who already got the campaigns it continues, again right before it starts." if m["continues"] else "")
            + " If Gmail's limit has no room then, MailSender's pacer holds it and sends as room frees up.")


def t_pause_campaign(a):
    c = campaign_of(a.get("campaign"))
    done, _ = set_status(c, "paused")
    return f"Paused campaign #{done['id']} {done['name']}; start_campaign carries on where it stopped."


def t_cancel_campaign(a):
    c = campaign_of(a.get("campaign"))
    done, _ = set_status(c, "cancelled")
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
    """MailSender's whole database, saved before anything is deleted; the file's path, or "" without LISTMONK_BACKUP."""
    if not BACKUP:
        return ""
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    target = os.path.join(BACKUP_DIR, f"listmonk-before-{why}-{time.strftime('%Y%m%d-%H%M%S')}.sql")
    with open(target, "wb") as fh:
        done = subprocess.run(shlex.split(BACKUP), stdout=fh, stderr=subprocess.PIPE, timeout=900)
    if done.returncode != 0 or os.path.getsize(target) < 1000:
        os.remove(target)
        raise Fail("could not save MailSender's database first, so nothing was deleted: " + done.stderr.decode("utf-8", "replace")[-300:])
    os.chmod(target, 0o600)
    return target


def t_delete_contacts(a):
    """Deletes contacts for good: everyone in the given lists or groups, except anyone also in a list to keep. The count must
    match, as for start_campaign, so the user approves the real number; the database is saved first."""
    targets = list_ids(a.get("lists"))
    if not targets:
        raise Fail("say whose contacts to delete: lists or groups such as cat:Περιοχές")
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
            + (f"MailSender's database was saved first in {saved}, so it can be restored." if saved else "There is no backup (LISTMONK_BACKUP is not set)."))


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
LISTS = {"type": "array", "items": {"type": "string"}, "description": "List names or ids, or whole groups: \"Γονείς › Θεσσαλονίκη\" (every list there), \"› Θεσσαλονίκη\" (that city in any category), \"cat:Γονείς\""}
CAMPAIGN = {"type": "string", "description": "Campaign id or exact name"}


def tool(name, description, props=None, required=()):
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": props or {}, "required": list(required)}}


MANAGE = [
    (tool("overview", "What is in MailSender (listmonk): contact and email counts, every list with its size, recent campaigns, the last import. Start here."), t_overview),
    (tool("create_list", "Make a mailing list.", {"name": S, "description": S, "optin": {"type": "string", "enum": ["single", "double"], "description": "single (default; double opt-in emails cannot be confirmed in local mode)"},
          "type": {"type": "string", "enum": ["private", "public"], "description": "private (default) or public"}, "category": {**S, "description": "MailSender category to group it under"},
          "subcategory": {**S, "description": "Not used any more: MailSender no longer shows list sub-categories; areas are contact tags (tag_contacts). Leave it out."},
          "tags": {"type": "array", "items": S}}, ["name"]), t_create_list),
    (tool("set_list_group", "Put lists in a MailSender category, like its Move to category. (Sub-categories are no longer used by MailSender: give "
          "subcategory \"\" to remove an old one; areas are contact tags, see tag_contacts.) A value left out stays as it is; an empty string removes it.", {"lists": LISTS, "category": S, "subcategory": S}, ["lists"]), t_set_list_group),
    (tool("find_contacts", "Find contacts by name or email text, list, tag (a region, e.g. Καλαμαριά), status or subscription status. Shows each contact's lists and attributes.",
          {"search": S, "list": LISTS, "tag": {**S, "description": "Contacts with this tag, e.g. \"Θεσσαλονίκη/Καλαμαριά\" or \"Καλαμαριά\""}, "status": {"type": "string", "enum": ["enabled", "disabled", "blocklisted"]},
           "subscription_status": {"type": "string", "enum": ["confirmed", "unconfirmed", "unsubscribed"]}, "limit": {"type": "integer"}, "page": {"type": "integer"}}), t_find_contacts),
    (tool("add_contact", "Add one contact, optionally to lists (confirmed unless confirmed is false).",
          {"email": S, "name": S, "lists": LISTS, "attribs": {"type": "object", "description": "Extra fields, e.g. {\"city\": \"Αθήνα\"}"}, "confirmed": {"type": "boolean"}}, ["email"]), t_add_contact),
    (tool("update_contact", "Change a contact's name, email, attributes (merged) or status (enabled, disabled, blocklisted). Their lists "
          "and subscriptions (unsubscribed ones too) stay as they are. For tags use tag_contacts.",
          {"email": {**S, "description": "Their current address"}, "new_email": S, "name": S, "attribs": {"type": "object"}, "status": S}, ["email"]), t_update_contact),
    (tool("list_tags", "MailSender's contact tags (regions under a city, e.g. Θεσσαλονίκη/Καλαμαριά), grouped by city, with how many contacts have each."), t_list_tags),
    (tool("tag_contacts", "Add or remove one contact tag (a region, optionally under a city) for contacts given by email, or for everyone in lists "
          "and/or everyone with another tag. Only the contacts' attributes change; their lists and subscriptions stay as they are.",
          {"tag": {**S, "description": "\"Θεσσαλονίκη/Καλαμαριά\" or \"Καλαμαριά (Θεσσαλονίκη)\"; or give region and city"},
           "region": S, "city": S, "action": {"type": "string", "enum": ["add", "remove"]},
           "emails": {"type": "array", "items": S}, "lists": {**LISTS, "description": "Everyone in these lists or groups"},
           "with_tag": {**S, "description": "Everyone who has this other tag"}}), t_tag_contacts),
    (tool("fill_tag_list", "Campaigns go to lists, so to email everyone with a tag: fills the list \"Region (City)\" (MailSender category Ετικέτες) with "
          "exactly the contacts that have the tag now, then create_campaign can use it. Nothing is sent.",
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
    (tool("create_campaign", "Make a draft campaign. Nothing is sent. Give lists and/or to_tags (areas: contacts with these tags; MailSender fills "
          "them in right before sending, without anyone who unsubscribed). body is HTML by default (content_type html, markdown, richtext or plain); the "
          "template wraps it with the logo and footer (the default template unless one is named). Personalise with {{ .Subscriber.Name }} and "
          "{{ .Subscriber.Attribs.city }}.", {"name": S, "subject": S, "lists": LISTS, "body": S, "content_type": S, "template": S,
          "tags": {"type": "array", "items": S}, "from_email": {**S, "description": "Leave out for listmonk's default sender"}, "altbody": {**S, "description": "Plain-text version"},
          "to_tags": {"type": "array", "items": S, "description": "Areas: contact tags such as \"ΘΕΣΣΑΛΟΝΙΚΗ/ΚΑΛΑΜΑΡΙΑ\" or \"ΚΑΛΑΜΑΡΙΑ\" (see list_tags); [] removes them"},
          "continues": {"type": "array", "items": S, "description": "Earlier campaigns (ids or names) this one continues: whoever already got them, or is still to get them (also a draft made just before, to send first), is left out, so nobody gets the same email twice (a second round, another area, the rest of a list). [] removes it."}},
          ["name", "subject", "body"]), t_create_campaign),
    (tool("update_campaign", "Change a draft, paused or scheduled campaign: any of its fields, including its areas (to_tags). MailSender's own "
          "settings on it (open tracking, areas, its helper list) are kept. Nothing is sent.",
          {"campaign": CAMPAIGN, "name": S, "subject": S, "lists": LISTS, "body": S, "content_type": S, "template": S, "tags": {"type": "array", "items": S}, "from_email": S, "altbody": S,
           "to_tags": {"type": "array", "items": S, "description": "Areas: contact tags such as \"ΘΕΣΣΑΛΟΝΙΚΗ/ΚΑΛΑΜΑΡΙΑ\" or \"ΚΑΛΑΜΑΡΙΑ\" (see list_tags); [] removes them"},
           "continues": {"type": "array", "items": S, "description": "Earlier campaigns (ids or names) this one continues: whoever already got them, or is still to get them (also a draft made just before, to send first), is left out, so nobody gets the same email twice (a second round, another area, the rest of a list). [] removes it."}},
          ["campaign"]), t_update_campaign),
    (tool("copy_campaign", "Copy a campaign to other recipients: the same email again (a second round, another area, the rest of a list), as "
          "MailSender's Αντίγραφο. Subject, text, template, sender, attachments and open-tracking settings stay; give the new recipients. "
          "It continues the original (and what that continued), so nobody who got it gets it again, unless continues says otherwise. "
          "Makes a draft; nothing is sent.",
          {"campaign": {**CAMPAIGN, "description": "The campaign to copy"}, "lists": LISTS,
           "to_tags": {"type": "array", "items": S, "description": "Areas (contact tags) for the copy"}, "name": S, "subject": {**S, "description": "Only to change it"},
           "tags": {"type": "array", "items": S},
           "continues": {"type": "array", "items": S, "description": "Leave out to continue the original and what it continued; [] for none"}},
          ["campaign"]), t_copy_campaign),
    (tool("check_already_sent", "Before sending the same email again (a copy, a second round, another area, the rest of a list): who among some "
          "recipients already got earlier campaigns, or is still to get them (paused or scheduled). Give the earlier campaigns and either the "
          "recipients (lists and/or to_tags) or a draft campaign. Then make the new campaign with continues set to those campaigns.",
          {"campaigns": {"type": "array", "items": S, "description": "The earlier campaigns, ids or names"}, "lists": LISTS,
           "to_tags": {"type": "array", "items": S, "description": "Areas (contact tags)"}, "campaign": {**CAMPAIGN, "description": "Or a campaign whose recipients to check"}},
          ["campaigns"]), t_check_already_sent),
    (tool("sending_capacity", "Gmail's daily sending limit as MailSender's pacer sees it: room now, any block after Gmail refused to send, what "
          "running, paused and scheduled campaigns still have to send, and when room frees up. Give emails to estimate when that many more "
          "would have gone out. Use it before planning when a campaign goes out.",
          {"emails": {"type": "integer", "description": "How many more emails to plan for (optional)"}}), t_sending_capacity),
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
    (tool("cancel_campaign", "Cancel a campaign for good. MailSender allows it to its admins only, so this is usually refused: then ask the user.", {"campaign": CAMPAIGN}, ["campaign"]), t_cancel_campaign),
    (tool("delete_contacts", "Delete contacts for good (not just from a list): everyone in the given lists or groups, except anyone also in a list "
          "in keep. count must be the exact number it deletes (if it is not, nothing is deleted and the answer gives the real number). Only when the user asked to delete "
          "contacts. The user approves every call, and MailSender's database is saved first.",
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
