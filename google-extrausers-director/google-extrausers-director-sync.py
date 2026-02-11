#!/usr/bin/env python3
"""
Sync Google Workspace Directory users (posixAccounts) into /var/lib/extrausers/{passwd,group,shadow}
with a local SQLite cache to minimize rewrites and keep request counts low.

Install:
  pip install google-auth google-auth-httplib2 google-api-python-client

Auth:
  - Service Account JSON with Domain-Wide Delegation (DWD)
  - Impersonate an admin subject with read rights
  - Scopes: https://www.googleapis.com/auth/admin.directory.user.readonly

Example:
  sudo ./gw_dir_to_extrausers_cached.py \
      --sa-key /etc/google/sa.json \
      --impersonate admin@yourdomain.com \
      --customer my_customer \
      --outdir /var/lib/extrausers \
      --db /var/lib/googleworkspace-idcache/users.db \
      --rps 5 --max-retries 5 --verbose
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES=["https://www.googleapis.com/auth/admin.directory.user.readonly",
        "https://www.googleapis.com/auth/admin.directory.group.readonly"]

current_version = (sys.version_info.major, sys.version_info.minor)
if sys.version_info.major >= 3:
    if sys.version_info.minor >= 12:
      NOW_ISO = dt.datetime.now(dt.UTC)
    else:
      NOW_ISO = dt.datetime.utcnow().isoformat(timespec="seconds")+"Z"
else:
    print("Python minimum major version : 3")
    sys.exit(1)

# -------------------- API + pacing --------------------
def get_directory_service(sa_key_path: str, subject: str):
    creds = service_account.Credentials.from_service_account_file(
        sa_key_path, scopes=SCOPES
    ).with_subject(subject)
    return build("admin", "directory_v1", credentials=creds, cache_discovery=False)


def pace(rps: float):
    # light pacing to stay well below per-user 10 rps limit
    if rps > 0:
        time.sleep(1.0 / rps + random.random() * 0.05)


def backoff_sleep(attempt: int):
    # attempt = 0,1,2,... exponential backoff with jitter (max ~32s)
    delay = min(32, (2 ** attempt)) + random.random()
    time.sleep(delay)


# -------------------- SQLite cache --------------------
DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT,
    username TEXT,
    uid INTEGER,
    gid INTEGER,
    gecos TEXT,
    home TEXT,
    shell TEXT,
    etag TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,       -- Admin SDK group "id" (stable)
    email TEXT NOT NULL,              -- group email
    name TEXT NOT NULL,               -- sanitized POSIX group name
    gid INTEGER NOT NULL UNIQUE,      -- allocated gid
    etag TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT
);

-- Many-to-many: group -> usernames
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    username TEXT NOT NULL,
    PRIMARY KEY (group_id, username),
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

-- Optional: record allocator cursors
CREATE TABLE IF NOT EXISTS allocators (
    key TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL
);

"""


def db_connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON;")
    for stmt in filter(None, DDL.split(";")):
        s = stmt.strip()
        if s:
            conn.execute(s)
    return conn


def meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    cur = conn.execute("SELECT value FROM meta WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def user_row_changed(db_row: Optional[tuple], new: dict) -> bool:
    """Compare cached row with new values we care about; return True if different."""
    if db_row is None:
        return True
    (
        _id,
        email,
        username,
        uid,
        gid,
        gecos,
        home,
        shell,
        etag,
        active,
        _updated_at,
    ) = db_row
    return any(
        [
            username != new["username"],
            email != new["email"],
            uid != new["uid"],
            gid != new["gid"],
            gecos != new["gecos"],
            home != new["home"],
            shell != new["shell"],
            etag != new.get("etag"),
            active != 1,
        ]
    )


def upsert_user(conn: sqlite3.Connection, record: dict):
    conn.execute(
        """
        INSERT INTO users(id, email, username, uid, gid, gecos, home, shell, etag, active, updated_at)
        VALUES(:id, :email, :username, :uid, :gid, :gecos, :home, :shell, :etag, 1, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
          username=excluded.username,
          email=excluded.email,
          uid=excluded.uid,
          gid=excluded.gid,
          gecos=excluded.gecos,
          home=excluded.home,
          shell=excluded.shell,
          etag=excluded.etag,
          active=1,
          updated_at=excluded.updated_at
        """,
        record,
    )


def deactivate_missing_users(conn: sqlite3.Connection, present_ids: List[str]) -> int:
    qmarks = ",".join("?" for _ in present_ids) or "''"
    cur = conn.execute(f"UPDATE users SET active=0 WHERE id NOT IN ({qmarks})", present_ids)
    return cur.rowcount


# -------------------- Helpers --------------------
def update_groups_db(svc, groups, conn, args):

    # Only user primary GIDs are external constraints; group GIDs are recomputed each run.
    used_gids = set()
    for (gid,) in conn.execute("SELECT gid FROM users WHERE active=1").fetchall():
        used_gids.add(int(gid))

    # Temporarily set all group GIDs to unique negative placeholders so that
    # the UNIQUE constraint on gid does not conflict during deterministic
    # reassignment (GIDs may shift when groups are added or removed).
    conn.execute("UPDATE groups SET gid = -ROWID")

    active_group_ids = []

    # Sort groups by their stable Google group ID so that collision resolution
    # (linear probing) is deterministic across independent service instances.
    for g in sorted(groups, key=lambda g: g["id"]):
        gid = deterministic_gid(g["id"], args.group_start_gid, args.group_end_gid, used_gids)

        gname = sanitize_groupname(g.get("email",""))

        conn.execute("""
          INSERT INTO groups(group_id,email,name,gid,etag,active,updated_at)
          VALUES(?,?,?,?,?,1,?)
          ON CONFLICT(group_id) DO UPDATE SET
            email=excluded.email,
            name=excluded.name,
            gid=excluded.gid,
            etag=excluded.etag,
            active=1,
            updated_at=excluded.updated_at
        """, (g["id"], g.get("email",""), gname, gid, g.get("etag"), NOW_ISO))

        active_group_ids.append(g["id"])

    # Mark missing groups inactive
    if active_group_ids:
        qmarks = ",".join("?" for _ in active_group_ids)
        conn.execute(f"UPDATE groups SET active=0 WHERE group_id NOT IN ({qmarks})", active_group_ids)

    # Build a map email->username from user cache (active users)
    email_to_username = {}
    cols = conn.execute("PRAGMA table_info(users)").fetchall()
    has_email = any(c[1] == "email" for c in cols)
    if has_email:
        for email, uname in conn.execute("SELECT email, username FROM users WHERE active=1").fetchall():
            if email:
                email_to_username[email.lower()] = uname

    # Refresh memberships (simple approach: clear + repopulate per group)
    for gid, email in conn.execute("SELECT group_id, email FROM groups WHERE active=1").fetchall():
        conn.execute("DELETE FROM group_members WHERE group_id=?", (gid,))

        members = list_group_members(svc, email, args.rps, args.max_retries)

        for m in members:
            m_email = (m.get("email") or "").lower()
            m_type  = (m.get("type")  or "").upper()
            m_stat  = (m.get("status") or "").upper()

            # Skip suspended/inactive membership entries
            if m_stat and m_stat not in ("ACTIVE",):
                continue

            if m_type == "USER":
                uname = email_to_username.get(m_email)
                if uname:
                    conn.execute("INSERT OR IGNORE INTO group_members(group_id, username) VALUES(?,?)", (gid, uname))
            else:
                # Currently, we don't support groups of groups.
                pass

    conn.commit()

def list_all_groups(svc, customer, domain, rps, max_retries):
    kwargs = {
        "maxResults": 200,
        "fields": "groups(id,email,name,etag),nextPageToken",
    }
    if domain:
        kwargs["domain"] = domain
    else:
        kwargs["customer"] = customer or "my_customer"

    groups = []
    req = svc.groups().list(**kwargs)
    while req is not None:
        pace(rps)
        for attempt in range(max_retries + 1):
            try:
                resp = req.execute(); break
            except HttpError as e:
                s = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                if s in (429,500,502,503,504) or "rateLimitExceeded" in str(e) or "userRateLimitExceeded" in str(e):
                    backoff_sleep(attempt); continue
                raise
        groups.extend(resp.get("groups", []))
        req = svc.groups().list_next(previous_request=req, previous_response=resp)
    return groups

def list_group_members(svc, group_email: str, rps: float, max_retries: int) -> list[dict]:
    kwargs = {
        "groupKey": group_email,
        "maxResults": 200,
        "fields": "members(email,type,status),nextPageToken",
    }
    members = []
    req = svc.members().list(**kwargs)
    while req is not None:
        pace(rps)
        for attempt in range(max_retries + 1):
            try:
                resp = req.execute(); break
            except HttpError as e:
                s = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                if s in (429,500,502,503,504) or "rateLimitExceeded" in str(e) or "userRateLimitExceeded" in str(e):
                    backoff_sleep(attempt); continue
                # common: 404 if group vanished between list and member fetch
                if s == 404:
                    return []
                raise
        members.extend(resp.get("members", []) or [])
        req = svc.members().list_next(previous_request=req, previous_response=resp)
    return members

def sanitize_groupname(email: str) -> str:
    # default: email local-part
    local = email.split("@")[0] if "@" in email else email
    name = "".join(c for c in local.lower() if c.isalnum() or c in ("-", "_", "."))
    return name


def deterministic_gid(group_id: str, start: int, end: int, used: set[int]) -> int:
    """Compute a deterministic GID from a Google Group ID via SHA-256 hashing.

    Maps the hash of the group_id into [start, end].  On collision with an
    already-claimed GID (user primary GID or earlier group in sorted order),
    probes linearly (wrapping) until a free slot is found.

    Because groups are processed in sorted order by group_id and the hash is
    deterministic, independent instances processing the same set of groups
    will always arrive at the same GID assignments.
    """
    range_size = end - start + 1
    h = int.from_bytes(
        hashlib.sha256(group_id.encode("utf-8")).digest()[:8], "big"
    )
    base = start + (h % range_size)

    for offset in range(range_size):
        candidate = start + (base - start + offset) % range_size
        if candidate not in used:
            used.add(candidate)
            return candidate

    raise RuntimeError(f"Out of group GIDs in range [{start},{end}]")

def fetch_users(svc, args):

    # Build request
    base_req = {
        "projection": "full",
        "maxResults": 200,  # Admin SDK max
        "orderBy": "email",
    }
    if args.domain:
        base_req["domain"] = args.domain
    else:
        base_req["customer"] = args.customer

    # Fetch users with pagination, pacing, and retries
    users: List[dict] = []
    req = svc.users().list(**base_req)
    while req is not None:
        # pacing
        pace(args.rps)
        for attempt in range(args.max_retries + 1):
            try:
                resp = req.execute()
                break
            except HttpError as e:
                code = getattr(e, "status_code", None)
                # Handle rate & 5xx-ish
                if e.resp and e.resp.status in (429, 500, 502, 503, 504) or (
                    "rateLimitExceeded" in str(e) or "userRateLimitExceeded" in str(e)
                ):
                    if attempt < args.max_retries:
                        if args.verbose:
                            print(f"Rate/Server error ({e.resp.status if e.resp else '??'}). Backing off (attempt {attempt+1})", file=sys.stderr)
                        backoff_sleep(attempt)
                        continue
                # other errors: fail
                raise
        users.extend(resp.get("users", []))
        req = svc.users().list_next(previous_request=req, previous_response=resp)

    if args.verbose:
        print(f"Fetched {len(users)} users", file=sys.stderr)

    return users

def update_users_db(users, conn, args):

    # First, mark all as inactive; we'll reactivate those we see (more efficient with NOT IN at end)
    present_ids: List[str] = []

    # Prepare UNIX data + detect shared primary GIDs
    gid_to_usernames: Dict[int, List[str]] = defaultdict(list)
    active_entries: List[dict] = []

    # Build current snapshot & update DB

    for u in users:
        if u.get("deleted") or u.get("suspended"):
            continue
        posix = pick_posix_account(u.get("posixAccounts", []))
        if not posix:
            continue
        uid = posix.get("uid")
        gid = posix.get("gid")
        if uid is None or gid is None:
            continue

        username = sanitize_username(posix.get("username") or u.get("primaryEmail", "").split("@")[0])
        full_name = (u.get("name") or {}).get("fullName") or username
        gecos = posix.get("gecos") or full_name
        shell = posix.get("shell") or args.default_shell
        home = posix.get("homeDirectory") or args.home_template.format(username=username)

        record = {
            "id": u["id"],
            "email": u["primaryEmail"],
            "username": username,
            "uid": int(uid),
            "gid": int(gid),
            "gecos": gecos,
            "home": home,
            "shell": shell,
            "etag": u.get("etag"),
            "updated_at": NOW_ISO,
        }

        # Compare with DB, upsert if changed
        cur = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],))
        row = cur.fetchone()
        if user_row_changed(row, record):
            upsert_user(conn, record)
        else:
            # even if unchanged, ensure active=1
            conn.execute("UPDATE users SET active=1 WHERE id=?", (u["id"],))

        present_ids.append(u["id"])
        gid_to_usernames[int(gid)].append(username)
        active_entries.append(record)

    # Deactivate users not present in current fetch
    deactivated = deactivate_missing_users(conn, present_ids) if present_ids else 0
    conn.commit()
    return active_entries, gid_to_usernames, deactivated

def sanitize_username(u: str) -> str:
    import re
    # lowercase and replace disallowed chars
    name = "".join(c for c in u.lower() if c.isalnum() or c in ("-", "_", "."))
    # strip "_example_com" or similar suffixes that Google appends
    name = re.sub(r'_[a-z0-9]+_com$', '', name)
    # truncate to 32 chars (Linux username max by default)
    return name[:32] or "user"


def pick_posix_account(posix_accounts: List[dict]) -> Optional[dict]:
    if not posix_accounts:
        return None
    prim = [p for p in posix_accounts if p.get("primary")]
    return prim[0] if prim else posix_accounts[0]


def atomic_write(path: str, content: str, mode: int = 0o640):
    dname = os.path.dirname(path)
    os.makedirs(dname, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dname, delete=False) as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.chmod(tmp_name, mode)
    os.replace(tmp_name, path)


def days_since_epoch() -> int:
    epoch = dt.date(1970, 1, 1)
    return (dt.date.today() - epoch).days


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# -------------------- Main sync --------------------
def main():
    ap = argparse.ArgumentParser(description="Sync Directory API users to extrausers with SQLite caching.")
    ap.add_argument("--sa-key", required=True, help="Path to service account JSON key.")
    ap.add_argument("--impersonate", required=True, help="Admin user to impersonate.")
    ap.add_argument("--customer", default="my_customer", help="Customer ID or 'my_customer'.")
    ap.add_argument("--domain", default=None, help="Optional: restrict to a specific domain.")
    ap.add_argument("--outdir", default="/var/lib/extrausers", help="Output directory for passwd/group/shadow.")
    ap.add_argument("--db", default="/var/lib/googleworkspace-idcache/users.db", help="SQLite cache path.")
    ap.add_argument("--default-shell", default="/bin/bash", help="Default shell if missing in posixAccounts.")
    ap.add_argument("--home-template", default="/home/{username}", help="Template for home dir if missing.")
    ap.add_argument("--group-sync", action="store_true", help="Sync Google Groups")
    ap.add_argument("--group-start-gid", type=int, default=30000, help="Starting gid for Google Groups -> POSIX Groups")
    ap.add_argument("--group-end-gid", type=int, default=39999, help="Ending gid for Google Groups -> POSIX Groups")
    ap.add_argument("--rps", type=float, default=5.0, help="Max requests per second (API pacing).")
    ap.add_argument("--max-retries", type=int, default=5, help="Max retries on rate/5xx errors.")
    ap.add_argument("--dry-run", action="store_true", help="Print would-be files; do not write.")
    ap.add_argument("--verbose", action="store_true", help="Verbose logs to stderr.")
    args = ap.parse_args()

    # Build service
    try:
        svc = get_directory_service(args.sa_key, args.impersonate)
    except Exception as e:
        print(f"ERROR: failed to create Directory service: {e}", file=sys.stderr)
        sys.exit(1)

    # DB connect
    conn = db_connect(args.db)

    users = fetch_users(svc, args)

    active_entries, gid_to_usernames, deactivated = update_users_db(users, conn, args)

    if args.group_sync:
        if args.verbose:
            print("Getting groups")
        groups = list_all_groups(svc, args.customer if not args.domain else None, args.domain, args.rps, args.max_retries)
        update_groups_db(svc, groups, conn, args)


    ####### Render extrausers files ########
    # Compose groups dict (gid->name, members empty; primary implied)
    groups: Dict[int, Tuple[str, set]] = {}
    for gid, members in gid_to_usernames.items():
        grpname = members[0] if len(members) == 1 else f"grp{gid}"
        groups[gid] = (grpname, set())

    # Build passwd, shadow, group for *active* users from DB to be authoritative
    cur = conn.execute(
        "SELECT username, uid, gid, gecos, home, shell FROM users WHERE active=1 ORDER BY uid, username"
    )
    rows = cur.fetchall()

    passwd_lines: List[str] = []
    shadow_lines: List[str] = []
    for username, uid, gid, gecos, home, shell in rows:
        passwd_lines.append(f"{username}:x:{uid}:{gid}:{gecos}:{home}:{shell}")
        lastchg = days_since_epoch()
        shadow_lines.append(f"{username}:!:{lastchg}:0:99999:7:::")

    group_lines: List[str] = []
    for gid in sorted(groups.keys()):
        name, members = groups[gid]
        members_csv = ",".join(sorted(members)) if members else ""
        group_lines.append(f"{name}:x:{gid}:{members_csv}")

    # Add google groups
    grows = conn.execute("SELECT group_id, name, gid FROM groups WHERE active=1 ORDER BY gid, name").fetchall()
    for group_id, gname, gid in grows:
        members = [u for (u,) in conn.execute(
            "SELECT username FROM group_members WHERE group_id=? ORDER BY username", (group_id,)
        ).fetchall()]
        group_lines.append(f"{gname}:x:{gid}:{','.join(members)}")

    passwd_txt = "\n".join(passwd_lines) + ("\n" if passwd_lines else "")
    group_txt = "\n".join(group_lines) + ("\n" if group_lines else "")
    shadow_txt = "\n".join(shadow_lines) + ("\n" if shadow_lines else "")

    # Change detection via snapshot hash (fast path)
    snapshot_hash = sha256(passwd_txt + "\n--\n" + group_txt + "\n--\n" + shadow_txt)
    prev_hash = meta_get(conn, "last_snapshot_hash")
    changed = snapshot_hash != prev_hash

    if args.verbose:
        print(
            f"Active users: {len(rows)} | groups: {len(groups)}+{len(grows)} | changed: {changed} "
            f"| deactivated this run: {deactivated}",
            file=sys.stderr,
        )

    out_passwd = os.path.join(args.outdir, "passwd")
    out_group = os.path.join(args.outdir, "group")
    out_shadow = os.path.join(args.outdir, "shadow")

    if args.dry_run:
        print("# ---- PASSWD ----")
        print(passwd_txt, end="")
        print("# ---- GROUP ----")
        print(group_txt, end="")
        print("# ---- SHADOW ----")
        print(shadow_txt, end="")
    else:
        if changed:
            # Typical perms for extrausers:
            #   passwd: 0644, group: 0644, shadow: 0640
            atomic_write(out_passwd, passwd_txt, 0o644)
            atomic_write(out_group, group_txt, 0o644)
            atomic_write(out_shadow, shadow_txt, 0o640)
            meta_set(conn, "last_snapshot_hash", snapshot_hash)
            conn.commit()
            if args.verbose:
                print("Wrote updated extrausers files.", file=sys.stderr)
        else:
            if args.verbose:
                print("No changes detected; skipped writing extrausers files.", file=sys.stderr)

    conn.close()


# -------------------- Entrypoint --------------------
if __name__ == "__main__":
    try:
        main()
    except HttpError as e:
        print(f"ERROR: Directory API call failed: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(99)
