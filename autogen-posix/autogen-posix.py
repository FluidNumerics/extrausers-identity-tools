#!/usr/bin/env python3
"""
Populate Google Workspace / Cloud Identity users with posixAccounts if missing.

- Scans all users (by domain or customer).
- Skips users that already have *any* posixAccounts entry.
- Assigns UID/GID from configurable starting values without collisions.
- Writes posixAccounts via Users.patch (only when --commit is set).
- By default sets gid = uid (user-private groups), configurable.

Install:
  pip install google-auth google-auth-httplib2 google-api-python-client

Example (dry run):
  ./autogen-posix.py \
    --sa-key /etc/google/sa.json \
    --impersonate admin@yourdomain.com \
    --customer my_customer \
    --start-uid 20000 --start-gid 20000 \
    --home-template /home/{username} \
    --default-shell /bin/bash

Apply changes:
  ... add --commit
"""

import argparse
import random
import re
import sys
import time
from typing import Dict, List, Set, Tuple, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPE_USER_RW = "https://www.googleapis.com/auth/admin.directory.user"


# ---------- Auth / API helpers ----------

def get_directory_service(sa_key_path: str, subject: str):
    creds = service_account.Credentials.from_service_account_file(
        sa_key_path, scopes=[SCOPE_USER_RW]
    ).with_subject(subject)
    return build("admin", "directory_v1", credentials=creds, cache_discovery=False)


def backoff_sleep(attempt: int):
    # Exponential backoff with jitter, capped.
    delay = min(32, 2 ** attempt) + random.random()
    time.sleep(delay)


# ---------- Username sanitization ----------

def sanitize_username(raw: str, strip_suffix: Optional[str]) -> str:
    """
    Lowercase, keep [a-z0-9._-], optionally strip a domain-derived suffix like '_mydomain_com'.
    """
    name = "".join(c for c in raw.lower() if c.isalnum() or c in ("-", "_", "."))
    if strip_suffix:
        if name.endswith(strip_suffix.lower()):
            name = name[: -len(strip_suffix)]
    else:
        # Generic pattern: remove "_example_com" style suffixes (safe default)
        name = re.sub(r"_[a-z0-9]+_com$", "", name)
    # Trim to 32 chars (typical Linux username limit)
    return name[:32] or "user"


def unique_username(base: str, taken: Set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    # Append -N until free
    i = 1
    while True:
        cand = f"{base}-{i}"
        if cand not in taken:
            taken.add(cand)
            return cand
        i += 1


# ---------- ID allocation ----------

def next_free(start: int, used: Set[int]) -> int:
    n = start
    while n in used:
        n += 1
    used.add(n)
    return n


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Populate missing posixAccounts for Workspace users.")
    ap.add_argument("--sa-key", required=True, help="Path to service account JSON key.")
    ap.add_argument("--impersonate", required=True, help="Admin user to impersonate.")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--customer", help="Customer ID or 'my_customer'.")
    grp.add_argument("--domain", help="Restrict to a specific domain (e.g., example.com).")

    ap.add_argument("--start-uid", type=int, default=20000, help="Starting UID to allocate from.")
    ap.add_argument("--start-gid", type=int, default=20000, help="Starting GID to allocate from (ignored if --gid-equals-uid).")
    ap.add_argument("--gid-equals-uid", action="store_true", default=True, help="Assign GID = UID (user-private groups).")
    ap.add_argument("--no-gid-equals-uid", dest="gid_equals_uid", action="store_false")
    ap.add_argument("--default-shell", default="/bin/bash", help="Default shell for new posixAccounts.")
    ap.add_argument("--home-template", default="/home/{username}", help="Home dir template (use {username}).")
    ap.add_argument("--strip-suffix", default=None, help="Optional username suffix to strip (e.g., _mydomain_com).")
    ap.add_argument("--rps", type=float, default=5.0, help="Pacing for API calls (requests/sec).")
    ap.add_argument("--max-retries", type=int, default=5, help="Max retries on 429/5xx.")
    ap.add_argument("--dry-run", action="store_true", help="Alias for not --commit (print plan only).")
    ap.add_argument("--commit", action="store_true", help="Perform updates (default is dry-run).")
    ap.add_argument("--verbose", action="store_true", help="Verbose logging.")
    args = ap.parse_args()
    if args.dry_run:
        args.commit = False

    svc = get_directory_service(args.sa_key, args.impersonate)

    # Prepare request to list users with minimal necessary fields
    list_kwargs = dict(
        projection="full",
        maxResults=200,
        orderBy="email",
        fields="users(id,primaryEmail,name/fullName,suspended,deleted,posixAccounts,etag),nextPageToken",
    )
    if args.domain:
        list_kwargs["domain"] = args.domain
    else:
        list_kwargs["customer"] = args.customer

    # Collect existing UIDs/GIDs to avoid collisions; also collect users missing posixAccounts
    used_uids: Set[int] = set()
    used_gids: Set[int] = set()
    missing: List[Dict] = []
    taken_usernames: Set[str] = set()

    # First pass: scan all users
    req = svc.users().list(**list_kwargs)
    while req is not None:
        # Pacing
        if args.rps > 0:
            time.sleep(1.0 / args.rps)

        # Execute with backoff
        last_exc = None
        for attempt in range(args.max_retries + 1):
            try:
                resp = req.execute()
                break
            except HttpError as e:
                last_exc = e
                # Retry on 429/5xx, rate/user limits
                status = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                msg = str(e)
                if status in (429, 500, 502, 503, 504) or "rateLimitExceeded" in msg or "userRateLimitExceeded" in msg:
                    if args.verbose:
                        print(f"[WARN] list users backoff attempt {attempt+1}: {status}", file=sys.stderr)
                    backoff_sleep(attempt)
                    continue
                raise
        else:
            raise last_exc  # exhausted retries

        for u in resp.get("users", []):
            if u.get("deleted") or u.get("suspended"):
                continue

            posix_list = u.get("posixAccounts", []) or []
            if posix_list:
                # harvest used IDs and taken usernames
                for pa in posix_list:
                    try:
                        uid = int(pa.get("uid")) if pa.get("uid") is not None else None
                        gid = int(pa.get("gid")) if pa.get("gid") is not None else None
                        if uid is not None:
                            used_uids.add(uid)
                        if gid is not None:
                            used_gids.add(gid)
                        uname = pa.get("username")
                        if uname:
                            taken_usernames.add(uname.lower())
                    except (TypeError, ValueError):
                        pass
                continue

            # No posixAccounts → candidate to populate
            primary_email = u.get("primaryEmail", "")
            local = primary_email.split("@")[0] if "@" in primary_email else primary_email
            base_username = sanitize_username(local, args.strip_suffix)
            # don't finalize yet; uniqueness enforced after full scan
            missing.append({
                "id": u["id"],
                "primaryEmail": primary_email,
                "fullName": (u.get("name") or {}).get("fullName") or base_username,
                "baseUsername": base_username,
            })

        req = svc.users().list_next(previous_request=req, previous_response=resp)

    if args.verbose:
        print(f"[INFO] scanned users; missing posixAccounts for {len(missing)} users", file=sys.stderr)

    # Allocate usernames/UIDs/GIDs without collisions
    planned: List[Tuple[str, Dict]] = []  # (user_id, posix body)
    next_uid = args.start_uid
    next_gid = args.start_gid
    # Ensure username uniqueness considers existing ones and those we will add in this run
    local_taken = set(taken_usernames)

    for m in missing:
        uname = unique_username(m["baseUsername"], local_taken)
        # Allocate UID
        uid = next_free(max(next_uid, args.start_uid), used_uids)
        next_uid = uid + 1
        # Allocate GID
        if args.gid_equals_uid:
            gid = uid
            used_gids.add(gid)
            if next_gid <= gid:
                next_gid = gid + 1
        else:
            gid = next_free(max(next_gid, args.start_gid), used_gids)
            next_gid = gid + 1

        home = args.home_template.format(username=uname)
        shell = args.default_shell
        gecos = m["fullName"]

        posix_obj = {
            "primary": True,
            "username": uname,
            "uid": uid,
            "gid": gid,
            "homeDirectory": home,
            "shell": shell,
            "gecos": gecos,
        }
        planned.append((m["id"], posix_obj))

    # Report plan
    if not planned:
        print("No users need posixAccounts. Nothing to do.")
        return

    print(f"Planned assignments for {len(planned)} users:")
    for user_id, posix in planned:
        print(f"  {user_id}: {posix['username']} uid={posix['uid']} gid={posix['gid']} home={posix['homeDirectory']} shell={posix['shell']}")

    if not args.commit:
        print("\nDRY RUN (no changes made). Re-run with --commit to apply.")
        return

    # Apply via users.patch
    updated = 0
    for user_id, posix in planned:
        # Pacing
        if args.rps > 0:
            time.sleep(1.0 / args.rps)

        body = {"posixAccounts": [posix]}
        last_exc = None
        for attempt in range(args.max_retries + 1):
            try:
                _ = svc.users().patch(userKey=user_id, body=body).execute()
                updated += 1
                if args.verbose:
                    print(f"[OK] Updated {user_id} → {posix['username']} ({posix['uid']}:{posix['gid']})", file=sys.stderr)
                break
            except HttpError as e:
                last_exc = e
                status = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                msg = str(e)
                if status in (429, 500, 502, 503, 504) or "rateLimitExceeded" in msg or "userRateLimitExceeded" in msg:
                    if args.verbose:
                        print(f"[WARN] patch backoff attempt {attempt+1} for {user_id}: {status}", file=sys.stderr)
                    backoff_sleep(attempt)
                    continue
                # conflict / invalid → show and continue
                print(f"[ERROR] Failed to update {user_id}: {e}", file=sys.stderr)
                break
        else:
            print(f"[ERROR] Retries exhausted for {user_id}: {last_exc}", file=sys.stderr)

    print(f"\nDone. Updated {updated}/{len(planned)} users.")

if __name__ == "__main__":
    main()
