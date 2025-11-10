import base64
import json
import os
import random
import re
import time
from typing import Dict, List, Optional, Set, Tuple

import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import secretmanager

SCOPE_USER_RW = "https://www.googleapis.com/auth/admin.directory.user"

# ----------------- helpers -----------------
def backoff_sleep(attempt: int):
    delay = min(32, 2 ** attempt) + random.random()
    time.sleep(delay)

def sanitize_username(raw: str, strip_suffix: Optional[str]) -> str:
    name = "".join(c for c in raw.lower() if c.isalnum() or c in ("-", "_", "."))
    if strip_suffix:
        s = strip_suffix.lower()
        if name.endswith(s):
            name = name[: -len(s)]
    else:
        name = re.sub(r"_[a-z0-9]+_com$", "", name)
    return name[:32] or "user"

def unique_username(base: str, taken: Set[str]) -> str:
    if base not in taken:
        taken.add(base); return base
    i = 1
    while True:
        cand = f"{base}-{i}"
        if cand not in taken:
            taken.add(cand); return cand
        i += 1

def next_free(start: int, used: Set[int]) -> int:
    n = start
    while n in used:
        n += 1
    used.add(n)
    return n

def load_sa_credentials_from_secret(secret_resource_id: str, version: str = "latest"):
    # secret_resource_id like: projects/123/secrets/workspace-dwd-sa-key
    client = secretmanager.SecretManagerServiceClient()
    name = f"{secret_resource_id}/versions/{version}"
    payload = client.access_secret_version(request={"name": name}).payload.data
    info = json.loads(payload.decode("utf-8"))
    creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE_USER_RW])
    return creds

def get_directory_service(creds, subject: str):
    # Domain-wide delegation: impersonate admin subject
    delegated = creds.with_subject(subject)
    return build("admin", "directory_v1", credentials=delegated, cache_discovery=False)

# ----------------- core logic -----------------
def populate_posix_accounts(
    svc,
    *,
    customer: Optional[str],
    domain: Optional[str],
    start_uid: int,
    start_gid: int,
    gid_equals_uid: bool,
    default_shell: str,
    home_template: str,
    strip_suffix: Optional[str],
    rps: float,
    max_retries: int,
) -> Dict[str, int]:
    list_kwargs = dict(
        projection="full",
        maxResults=200,
        orderBy="email",
        fields="users(id,primaryEmail,name/fullName,suspended,deleted,posixAccounts,etag),nextPageToken",
    )
    if domain:
        list_kwargs["domain"] = domain
    else:
        list_kwargs["customer"] = customer or "my_customer"

    used_uids: Set[int] = set()
    used_gids: Set[int] = set()
    taken_usernames: Set[str] = set()
    missing: List[Dict] = []

    req = svc.users().list(**list_kwargs)
    while req is not None:
        if rps > 0: time.sleep(1.0 / rps)

        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                resp = req.execute()
                break
            except HttpError as e:
                last_exc = e
                s = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                if s in (429,500,502,503,504) or "rateLimitExceeded" in str(e) or "userRateLimitExceeded" in str(e):
                    backoff_sleep(attempt); continue
                raise
        else:
            raise last_exc

        for u in resp.get("users", []):
            if u.get("deleted") or u.get("suspended"): continue

            posix_list = u.get("posixAccounts", []) or []
            if posix_list:
                for pa in posix_list:
                    try:
                        uid = int(pa.get("uid")) if pa.get("uid") is not None else None
                        gid = int(pa.get("gid")) if pa.get("gid") is not None else None
                        if uid is not None: used_uids.add(uid)
                        if gid is not None: used_gids.add(gid)
                        uname = pa.get("username")
                        if uname: taken_usernames.add(uname.lower())
                    except (TypeError, ValueError):
                        pass
                continue

            primary_email = u.get("primaryEmail", "")
            local = primary_email.split("@")[0] if "@" in primary_email else primary_email
            base_username = sanitize_username(local, strip_suffix)
            missing.append({
                "id": u["id"],
                "primaryEmail": primary_email,
                "fullName": (u.get("name") or {}).get("fullName") or base_username,
                "baseUsername": base_username,
            })

        req = svc.users().list_next(previous_request=req, previous_response=resp)

    # Plan allocations
    next_uid = start_uid
    next_gid = start_gid
    local_taken = set(taken_usernames)
    planned: List[Tuple[str, Dict]] = []

    for m in missing:
        uname = unique_username(m["baseUsername"], local_taken)
        uid = next_free(max(next_uid, start_uid), used_uids); next_uid = uid + 1
        if gid_equals_uid:
            gid = uid; used_gids.add(gid); next_gid = max(next_gid, gid + 1)
        else:
            gid = next_free(max(next_gid, start_gid), used_gids); next_gid = gid + 1

        posix_obj = {
            "primary": True,
            "username": uname,
            "uid": uid,
            "gid": gid,
            "homeDirectory": home_template.format(username=uname),
            "shell": default_shell,
            "gecos": m["fullName"],
        }
        planned.append((m["id"], posix_obj))

    # Apply
    updated = 0
    for user_id, posix in planned:
        if rps > 0: time.sleep(1.0 / rps)
        body = {"posixAccounts": [posix]}
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                _ = svc.users().patch(userKey=user_id, body=body).execute()
                updated += 1
                break
            except HttpError as e:
                last_exc = e
                s = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                if s in (429,500,502,503,504) or "rateLimitExceeded" in str(e) or "userRateLimitExceeded" in str(e):
                    backoff_sleep(attempt); continue
                # non-retryable
                break
        else:
            # exhausted retries; skip
            pass

    return {"updated": updated, "planned": len(planned)}

# ----------------- Cloud Function entry -----------------
def run(event=None, context=None):
    # Pull env
    imp = os.environ["IMPERSONATE_EMAIL"]
    customer = os.environ.get("CUSTOMER") or "my_customer"
    domain = os.environ.get("DOMAIN") or ""
    start_uid = int(os.environ.get("START_UID", "20000"))
    start_gid = int(os.environ.get("START_GID", "20000"))
    gid_equals_uid = os.environ.get("GID_EQUALS_UID", "true").lower() == "true"
    default_shell = os.environ.get("DEFAULT_SHELL", "/bin/bash")
    home_template = os.environ.get("HOME_TEMPLATE", "/home/{username}")
    strip_suffix = os.environ.get("STRIP_SUFFIX") or None
    rps = float(os.environ.get("RPS", "5"))
    max_retries = int(os.environ.get("MAX_RETRIES", "5"))

    secret_resource_id = os.environ["SECRET_RESOURCE_ID"]
    secret_version = os.environ.get("SECRET_VERSION", "latest")

    # Load DWD service account key from Secret Manager
    creds = load_sa_credentials_from_secret(secret_resource_id, secret_version)
    svc = get_directory_service(creds, subject=imp)

    result = populate_posix_accounts(
        svc,
        customer=customer if not domain else None,
        domain=domain if domain else None,
        start_uid=start_uid,
        start_gid=start_gid,
        gid_equals_uid=gid_equals_uid,
        default_shell=default_shell,
        home_template=home_template,
        strip_suffix=strip_suffix,
        rps=rps,
        max_retries=max_retries,
    )

    print(json.dumps({"status": "ok", **result}))
    return {"status": "ok", **result}
