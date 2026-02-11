# Google ExtraUsers Sync Service

## Google Workspace Users → POSIX User Synchronization

This service synchronizes **Google Workspace / Cloud Identity users** into **POSIX users** exposed on Linux systems via `nss_extrausers`.

The director service periodically queries Google Workspace, resolves POSIX identity attributes, and renders:

```
/var/lib/extrausers/passwd
/var/lib/extrausers/shadow
```

---

### Source of Truth

| Attribute      | Source                                              |
| -------------- | --------------------------------------------------- |
| User identity  | Google Workspace Directory API                      |
| Login name     | Derived from Workspace primary email                |
| UID / GID      | Workspace `posixAccounts` **or** director allocator |
| Home directory | Workspace `posixAccounts` or configured default     |
| Shell          | Workspace `posixAccounts` or configured default     |
| Password hash  | Not stored (accounts are locked locally)            |

The **Google user `id`** is treated as the stable identifier.
Email renames do not create new POSIX users or change numeric IDs.

---

### Username Mapping

POSIX usernames are derived from the Workspace user’s `posixAccounts.username` field. If a Workspace user does not have a `posixAccounts` field defined, the user is not imported.

Rules:
* lower-case
* characters limited to `[a-z0-9._-]`
* max length: 32 characters

The resolved username is stored in the local cache and remains stable.

---

### UID / GID Resolution

This service only supports Workspace accounts in which the `posixAccounts` field is defined.

If a user already has a Workspace `posixAccounts` entry:

* `uid`
* `gid`
* `homeDirectory`
* `shell`
* `gecos`

These values are treated as **authoritative** and mirrored locally.

---

### Account State Mapping

| Workspace State | POSIX Result                   |
| --------------- | ------------------------------ |
| Active          | Present in extrausers          |
| Suspended       | Removed from extrausers output |
| Deleted         | Removed from extrausers output |

Removed users disappear from `passwd`/`shadow` on the next sync run.

---

### Shadow File Behavior

* All users are rendered with **locked passwords**:

  ```
  username:!:...
  ```
* No password hashes are stored locally
* Authentication is expected to occur via:

  * SSH (keys, Tailscale SSH, etc.)
  * external identity providers
  * PAM modules independent of `/etc/shadow`

This avoids credential duplication and local password drift.

---

### Sync Behavior

On each sync run:

1. List Workspace users
2. Resolve or provision POSIX identity
3. Update local SQLite cache
4. Render:

   * `/var/lib/extrausers/passwd`
   * `/var/lib/extrausers/shadow`
5. Publish bundle for agent nodes

Changes in Workspace are reflected cluster-wide on the next sync interval.

---

### Permissions Required

The director service account (with Domain-Wide Delegation) requires:

```text
admin.directory.user.readonly
```

---

## Google Groups → POSIX Group Synchronization

This service synchronizes **Google Workspace / Cloud Identity groups** into **POSIX groups** exposed on Linux systems via `nss_extrausers`.

### Overview

* Google Groups **do not natively store a POSIX GID**
* The director service **deterministically derives** POSIX group IDs from each group's Google identity
* Group membership is resolved from Google Workspace and rendered into:

  ```
  /var/lib/extrausers/group
  ```

Agent nodes periodically pull the rendered files from one of the director nodes.

---

### Source of Truth

| Attribute        | Source                           |
| ---------------- | -------------------------------- |
| Group identity   | Google Workspace Directory API   |
| Group members    | Google Workspace Members API     |
| POSIX GID        | Deterministic hash of Google group ID |
| POSIX group name | Derived from Google group email  |
| Group membership | Resolved user → username mapping |

The **Google group `id`** (not email) is used as the stable key.
This allows group email renames without changing the assigned GID.

---

### GID Allocation Model

GIDs are computed **deterministically** from the Google Group's stable `id` using SHA-256 hashing, mapped into a configured numeric range:

  ```ini
  GROUP_START_GID=30000
  GROUP_END_GID=39999
  ```

* The same Google Group always produces the same GID, regardless of which director instance computes it
* Independent instances with separate databases will arrive at identical GID assignments for the same set of groups
* Collisions with user primary GIDs or other groups are resolved via deterministic linear probing (groups processed in sorted order by Google group ID)
* GIDs are recomputed each sync run — they depend only on the org's groups and user primary GIDs, not on local database history

---

### Group Name Mapping

By default, the POSIX group name is derived from the **local-part of the group email**:

```
research-team@example.org  →  research-team
```

Rules:

* lower-case
* characters limited to `[a-z0-9._-]`
* max length: 32 characters
* optional suffix stripping (e.g. `_example_org`)
* collisions are resolved by appending `-N` (`team`, `team-1`, `team-2`)

The resolved name is stored in the cache to ensure stability.

---

### Membership Resolution

* `members.list` is used to retrieve group members
* Only `type=USER` memberships are included by default
* Member email addresses are mapped to local POSIX usernames using the cached user table
* Nested groups (`type=GROUP`) are ignored unless explicitly enabled

Resulting entry format:

```
groupname:x:<gid>:user1,user2,user3
```

---

### Sync Behavior

On each sync run:

1. List all Google groups
2. Compute deterministic GIDs via hashing
3. Update group metadata in SQLite
4. Resolve and cache group memberships
5. Render `/var/lib/extrausers/group`
6. Publish bundle for agent nodes

Groups or memberships removed in Google Workspace are marked inactive and removed from output on the next sync.

---

### Permissions Required

The director service account (with Domain-Wide Delegation) requires:

```text
admin.directory.group.readonly
admin.directory.group.member.readonly
admin.directory.user   (only if provisioning posixAccounts)
```

---
