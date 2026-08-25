"""Feed management for one operator.

Login is a magic link, but the link is issued over SSH rather than email:

    python3 -m givemesomegoodnews.admin --login

prints a one-time URL good for fifteen minutes. That avoids standing up an
outbound mail path — DigitalOcean blocks port 25, so email would mean a
third-party relay and an API key sitting on the box — while giving the same
passwordless, nothing-to-remember flow.

Edits are written to org_overrides, never to the orgs table alone, so that
re-seeding the catalog or re-importing the directory cannot quietly undo
them. seed.apply_overrides() replays them after every load.
"""

import html
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config
from .db import connect
from .filters import FIELDS as FILTER_FIELDS, load as load_filters
from .tags import COMMUNITY_TAGS, OWNERSHIP_TAGS, PRACTICE_TAGS

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "ford@ftrain.com")
TOKEN_MINUTES = 15
SESSION_DAYS = 30
COOKIE = "gmsgn_admin"
ALL_TAGS = sorted(set(OWNERSHIP_TAGS + COMMUNITY_TAGS + PRACTICE_TAGS))

EDITABLE_TEXT = [
    ("name", "Name"), ("url", "Website"), ("feed_url", "Feed URL"),
    ("support_url", "Donate/subscribe URL"), ("support_label", "Support label"),
    ("model", "Ownership model"), ("beat", "Beat"), ("city", "City"),
    ("state", "State"), ("coverage", "Coverage"), ("tagline", "Tagline"),
]


def _hash(value):
    return sha256(value.encode("utf-8")).hexdigest()


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


# --------------------------------------------------------------------- auth
def issue_token():
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_MINUTES)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM admin_tokens WHERE expires_at < now()")
        cur.execute(
            "INSERT INTO admin_tokens (token_hash, email, expires_at) VALUES (%s, %s, %s)",
            (_hash(token), ADMIN_EMAIL, expires),
        )
    return f"{config.SITE_URL}/admin/auth?t={token}", expires


def redeem(token):
    """Swap a valid one-time token for a session. Returns the session, or None."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT email FROM admin_tokens WHERE token_hash = %s "
            "AND used_at IS NULL AND expires_at > now()",
            (_hash(token),),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("UPDATE admin_tokens SET used_at = now() WHERE token_hash = %s",
                    (_hash(token),))
        session = secrets.token_urlsafe(32)
        cur.execute(
            "INSERT INTO admin_sessions (session_hash, email, expires_at) VALUES (%s, %s, %s)",
            (_hash(session), row[0], datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)),
        )
        return session


def session_email(cookie_header):
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and value:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM admin_sessions WHERE session_hash = %s AND expires_at > now()",
                    (_hash(value),),
                )
                row = cur.fetchone()
                if row:
                    return row[0]
    return None


def csrf_for(email):
    return sha256(f"{email}:{config.DATABASE_URL}".encode()).hexdigest()[:32]


# --------------------------------------------------------------------- views
CSS = """<style>
body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:0 auto;padding:1rem 1rem 4rem;
color:#111;background:#fff}
@media(prefers-color-scheme:dark){body{color:#eee;background:#111}
input,select,textarea{background:#1c1c1c;color:#eee;border-color:#444}}
a{color:#b3000f}
h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:1.6rem}
label{display:block;margin:.6rem 0 .15rem;font-size:.8rem;text-transform:uppercase;
letter-spacing:.05em;color:#666}
input[type=text],input[type=url],input[type=search]{width:100%;padding:.4rem;font:inherit;
border:1px solid #ccc}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.35rem .4rem;
border-bottom:1px solid #ddd;font-size:.9rem}
.tags{display:flex;flex-wrap:wrap;gap:.3rem .8rem;margin:.3rem 0}
.tags label{display:inline-flex;gap:.3rem;align-items:center;text-transform:none;
letter-spacing:0;font-size:.85rem;color:inherit;margin:0}
button{font:inherit;padding:.45rem .9rem;cursor:pointer;border:1px solid #b3000f;
background:#b3000f;color:#fff}
button.quiet{background:transparent;color:#b3000f}
.note{color:#666;font-size:.85rem}
</style>"""


def shell(title, body):
    return (f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow'>"
            f"<title>{esc(title)}</title>{CSS}</head><body>"
            f"<p class=note><a href='/admin'>Newsrooms</a> &middot; "
            f"<a href='/admin/filters'>Front-page filters</a> &middot; "
            f"<a href='/'>Back to the site</a></p>{body}</body></html>")


def list_view(email, query=""):
    with connect() as conn, conn.cursor() as cur:
        if query:
            cur.execute(
                "SELECT slug, name, state, model, feed_url, crawl_feed, in_default "
                "FROM orgs WHERE name ILIKE %s OR slug ILIKE %s ORDER BY name LIMIT 100",
                (f"%{query}%", f"%{query}%"),
            )
        else:
            # No search term: show what has been edited most recently.
            cur.execute(
                "SELECT o.slug, o.name, o.state, o.model, o.feed_url, o.crawl_feed, o.in_default "
                "FROM orgs o JOIN org_overrides v ON v.slug = o.slug "
                "ORDER BY v.updated_at DESC LIMIT 50"
            )
        rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM orgs")
        total = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM orgs WHERE NOT crawl_feed")
        paused = cur.fetchone()[0]

    body = [f"<h1>Feed management</h1>",
            f"<p class=note>{total} newsrooms, {paused} with crawling paused. "
            f"Signed in as {esc(email)}.</p>",
            "<form method=get action='/admin'><label for=q>Find a newsroom</label>"
            f"<input type=search id=q name=q value='{esc(query)}' "
            "placeholder='name or slug'><p><button type=submit>Search</button></p></form>"]
    body.append("<h2>" + ("Results" if query else "Recently edited") + "</h2>")
    if not rows:
        body.append("<p class=note>Nothing yet. Search for a newsroom to edit it.</p>")
    else:
        body.append("<table><tr><th>Newsroom</th><th>State</th><th>Model</th>"
                    "<th>Feed</th><th>Default</th></tr>")
        for slug, name, state, model, feed, crawl, default in rows:
            body.append(
                f"<tr><td><a href='/admin/org?slug={esc(slug)}'>{esc(name)}</a></td>"
                f"<td>{esc(state or '')}</td><td>{esc((model or '')[:26])}</td>"
                f"<td>{'paused' if not crawl else ('yes' if feed else 'none')}</td>"
                f"<td>{'yes' if default else 'no'}</td></tr>")
        body.append("</table>")
    return shell("Feed management", "".join(body))


def org_view(email, slug, message=""):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, name, url, feed_url, support_url, support_label, model, beat, "
            "city, state, coverage, tagline, features, in_default, crawl_feed, source "
            "FROM orgs WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("SELECT fields, updated_at FROM org_overrides WHERE slug = %s", (slug,))
        override = cur.fetchone()

    keys = ("slug", "name", "url", "feed_url", "support_url", "support_label", "model",
            "beat", "city", "state", "coverage", "tagline", "features", "in_default",
            "crawl_feed", "source")
    org = dict(zip(keys, row))
    features = set(org["features"] or [])

    fields = "".join(
        f"<label for={key}>{esc(label)}</label>"
        f"<input type=text id={key} name={key} value='{esc(org.get(key))}'>"
        for key, label in EDITABLE_TEXT
    )
    checks = "".join(
        f"<label><input type=checkbox name=features value='{esc(tag)}'"
        f"{' checked' if tag in features else ''}> {esc(tag)}</label>"
        for tag in ALL_TAGS
    )
    edited = ""
    if override:
        edited = (f"<p class=note>Overridden fields: "
                  f"{esc(', '.join(sorted((override[0] or {}).keys())))} "
                  f"(last edited {override[1]:%Y-%m-%d %H:%M} UTC). "
                  f"These survive re-seeding.</p>")

    body = [
        f"<h1>{esc(org['name'])}</h1>",
        f"<p class=note>{esc(org['slug'])} &middot; from {esc(org['source'] or 'curated')} "
        f"&middot; <a href='{esc(org['url'])}'>visit site</a></p>",
        f"<p class=note>{esc(message)}</p>" if message else "",
        edited,
        f"<form method=post action='/admin/save'>",
        f"<input type=hidden name=slug value='{esc(slug)}'>",
        f"<input type=hidden name=csrf value='{csrf_for(email)}'>",
        fields,
        "<label>Tags</label>", f"<div class=tags>{checks}</div>",
        "<div class=tags>",
        f"<label><input type=checkbox name=in_default value=1"
        f"{' checked' if org['in_default'] else ''}> Show in the default feed</label>",
        f"<label><input type=checkbox name=crawl_feed value=1"
        f"{' checked' if org['crawl_feed'] else ''}> Keep crawling this feed</label>",
        "</div>",
        "<p><button type=submit>Save</button></p></form>",
        f"<form method=post action='/admin/reset'>"
        f"<input type=hidden name=slug value='{esc(slug)}'>"
        f"<input type=hidden name=csrf value='{csrf_for(email)}'>"
        f"<p><button class=quiet type=submit>Reset to the catalog files</button></p></form>",
    ]
    return shell(org["name"], "".join(body))


def save(email, form):
    slug = (form.get("slug") or [""])[0]
    if not slug:
        return "no newsroom given"
    edits = {}
    for key, _label in EDITABLE_TEXT:
        value = (form.get(key) or [""])[0].strip()
        edits[key] = value or None
    edits["features"] = form.get("features", [])
    edits["in_default"] = bool(form.get("in_default"))
    edits["crawl_feed"] = bool(form.get("crawl_feed"))

    with connect() as conn, conn.cursor() as cur:
        assignments = ", ".join(f"{k} = %({k})s" for k in edits)
        params = dict(edits, slug=slug)
        cur.execute(f"UPDATE orgs SET {assignments} WHERE slug = %(slug)s", params)
        cur.execute(
            "INSERT INTO org_overrides (slug, fields, updated_by) VALUES (%s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET fields = EXCLUDED.fields, "
            "updated_at = now(), updated_by = EXCLUDED.updated_by",
            (slug, json.dumps(edits), email),
        )
    return "Saved. These edits survive the next re-seed."


def reset(email, form):
    """Drop the override and put back the fields seeding won't restore itself.

    Most columns are overwritten from the yaml on the next seed, but tagline
    is COALESCEd (so a null in yaml won't clear it) and crawl_feed is not a
    seeded field at all. Both are reset here explicitly.
    """
    slug = (form.get("slug") or [""])[0]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM org_overrides WHERE slug = %s", (slug,))
        cur.execute(
            "UPDATE orgs SET crawl_feed = true, tagline = NULL WHERE slug = %s", (slug,)
        )
    return "Reset. The next seed restores the catalog values."


def filters_view(email, message=""):
    """What the front page leaves out, and a way to change it."""
    with connect() as conn, conn.cursor() as cur:
        rules = load_filters(cur)
        counts = {}
        for rule in rules:
            cur.execute(
                f"SELECT count(*) FROM articles WHERE coalesce({rule['field']}, '') ~* %s",
                (rule["pattern"],),
            )
            counts[rule["id"]] = cur.fetchone()[0]

    rows = []
    for rule in rules:
        state = "on" if rule["enabled"] else "off"
        rows.append(
            f"<tr><td>{esc(rule['field'])}</td>"
            f"<td><code>{esc(rule['pattern'])}</code><br>"
            f"<span class=note>{esc(rule['note'] or '')}</span></td>"
            f"<td>{counts.get(rule['id'], 0)}</td>"
            f"<td>{state}</td>"
            f"<td><form method=post action='/admin/filter' style='display:inline'>"
            f"<input type=hidden name=csrf value='{csrf_for(email)}'>"
            f"<input type=hidden name=id value='{rule['id']}'>"
            f"<button class=quiet name=action value='toggle'>"
            f"{'disable' if rule['enabled'] else 'enable'}</button>"
            f"<button class=quiet name=action value='delete'>delete</button>"
            f"</form></td></tr>")

    options = "".join(f"<option value='{esc(f)}'>{esc(f)}</option>" for f in FILTER_FIELDS)
    body = [
        "<h1>Front-page filters</h1>",
        "<p class=note>Stories matching an enabled rule are kept off the feed "
        "pages. They stay in the database and stay searchable. Patterns are "
        "case-insensitive POSIX regexes; <code>\\y</code> is a word boundary. "
        "The count is how many stories in the archive each rule matches.</p>",
        f"<p class=note>{esc(message)}</p>" if message else "",
        "<table><tr><th>Field</th><th>Pattern</th><th>Matches</th>"
        "<th>State</th><th></th></tr>" + "".join(rows) + "</table>",
        "<h2>Add a rule</h2>",
        "<form method=post action='/admin/filter'>",
        f"<input type=hidden name=csrf value='{csrf_for(email)}'>",
        "<input type=hidden name=action value='add'>",
        f"<label for=field>Field</label><select id=field name=field>{options}</select>",
        "<label for=pattern>Pattern</label>"
        "<input type=text id=pattern name=pattern placeholder='\\yobituar'>",
        "<label for=note>Note</label><input type=text id=note name=note>",
        "<p><button type=submit>Add</button></p></form>",
    ]
    return shell("Front-page filters", "".join(body))


def filter_action(email, form):
    action = (form.get("action") or [""])[0]
    with connect() as conn, conn.cursor() as cur:
        if action == "add":
            field = (form.get("field") or [""])[0]
            pattern = (form.get("pattern") or [""])[0].strip()
            if field not in FILTER_FIELDS or not pattern:
                return "Need a valid field and a pattern."
            try:  # reject a regex Postgres cannot compile, before storing it
                cur.execute("SELECT %s ~* %s", ("test", pattern))
            except Exception:
                return "That is not a valid regular expression."
            cur.execute(
                "INSERT INTO feed_filters (field, pattern, note) VALUES (%s, %s, %s)",
                (field, pattern, (form.get("note") or [""])[0].strip() or None),
            )
            return "Rule added."
        rule_id = (form.get("id") or [""])[0]
        if not rule_id.isdigit():
            return "No rule given."
        if action == "toggle":
            cur.execute("UPDATE feed_filters SET enabled = NOT enabled WHERE id = %s", (rule_id,))
            return "Rule toggled."
        if action == "delete":
            cur.execute("DELETE FROM feed_filters WHERE id = %s", (rule_id,))
            return "Rule deleted."
    return "Nothing to do."


# ------------------------------------------------------------------ handler
class Handler(BaseHTTPRequestHandler):
    server_version = "givemesomegoodnews-admin"

    def _send(self, body, status=200, headers=()):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _deny(self):
        self._send(shell("Sign in", "<h1>Sign in</h1><p>This page needs a login link. "
                                    "Issue one on the server with "
                                    "<code>python3 -m givemesomegoodnews.admin --login</code>.</p>"),
                   status=403)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/admin/auth":
            token = (params.get("t") or [""])[0]
            session = redeem(token) if token else None
            if not session:
                return self._send(shell("Link expired",
                                        "<h1>That link has expired</h1><p>Issue a new one.</p>"),
                                  status=403)
            return self._send(
                shell("Signed in", "<h1>Signed in</h1><p><a href='/admin'>Feed management</a></p>"),
                headers=[("Set-Cookie",
                          f"{COOKIE}={session}; Path=/admin; HttpOnly; Secure; "
                          f"SameSite=Lax; Max-Age={SESSION_DAYS * 86400}")])

        email = session_email(self.headers.get("Cookie"))
        if not email:
            return self._deny()
        if parsed.path in ("/admin", "/admin/"):
            return self._send(list_view(email, (params.get("q") or [""])[0][:80]))
        if parsed.path in ("/admin/filters", "/admin/filters/"):
            return self._send(filters_view(email, (params.get("m") or [""])[0][:120]))
        if parsed.path == "/admin/org":
            view = org_view(email, (params.get("slug") or [""])[0][:80],
                            (params.get("m") or [""])[0][:120])
            return self._send(view) if view else self._send(
                shell("Not found", "<h1>No such newsroom</h1>"), status=404)
        self._send(shell("Not found", "<h1>Not found</h1>"), status=404)

    def do_POST(self):
        email = session_email(self.headers.get("Cookie"))
        if not email:
            return self._deny()
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            return self._send(shell("Too big", "<h1>Too much data</h1>"), status=413)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        if (form.get("csrf") or [""])[0] != csrf_for(email):
            return self._send(shell("Rejected", "<h1>Stale form</h1><p>Reload and try again.</p>"),
                              status=403)
        parsed = urlparse(self.path)
        if parsed.path == "/admin/filter":
            message = filter_action(email, form)
            self.send_response(303)
            self.send_header("Location", f"/admin/filters?m={message.replace(' ', '+')}")
            self.end_headers()
            return
        if parsed.path == "/admin/save":
            message = save(email, form)
        elif parsed.path == "/admin/reset":
            message = reset(email, form)
        else:
            return self._send(shell("Not found", "<h1>Not found</h1>"), status=404)
        slug = (form.get("slug") or [""])[0]
        self.send_response(303)
        self.send_header("Location", f"/admin/org?slug={slug}&m={message.replace(' ', '+')}")
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    if "--login" in sys.argv:
        url, expires = issue_token()
        print(f"\nOne-time login link (valid {TOKEN_MINUTES} minutes, until "
              f"{expires:%H:%M} UTC):\n\n  {url}\n")
        return
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8082
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
