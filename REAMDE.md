# Google ExtraUsers

This is a lightweight, simple tool that uses the google admin sdk to populate the `/etc/extrausers/{passwd,group,shadow}` files. When combined with `nss_extrausers`, this provides a simple scaleable alternative to Google Secure LDAP that mitigates quota issues.


## Components

The Google Directory API gives you **identity data** (users, groups), but **not password verification**. So you need two layers:

1. an **identity/NSS layer** to expose users & groups to Linux, and
2. an **auth/PAM layer** to actually authenticate people (Tailscale, OIDC/SAML or SSH-certs).


### Providing Identity (NSS) via a local sync cache

* **Create a Google Cloud project & service account** and enable **Admin SDK / Directory API**. Grant **domain-wide delegation (DWD)** to the service account with the least scopes you need (e.g., `admin.directory.user.readonly`, `admin.directory.group.readonly`). ([Google Cloud][1])

* **Pull users & groups** with the Directory API (paginated `users.list`, `groups.list`, `members.list`). Map Google users→Unix accounts and groups→Unix groups. ([Google for Developers][2])

* **Store a local cache** (e.g., SQLite under `/var/lib/gsuite-idcache`) containing:

  * `username` (POSIX), `uidNumber`, `gidNumber`, `gecos`, `home`, `shell`
  * group records and memberships
  * optional SSH pubkeys if you keep them in a custom schema/attribute on the Google side


* **Expose the cache to NSS** without running your own LDAP:

  * Easiest: install **`libnss-extrausers`** and write flat files to `/var/lib/extrausers/{passwd,group,shadow}` from your cache; add `extrausers` to `nsswitch.conf`. This avoids touching `/etc/passwd`. ([Unix & Linux Stack Exchange][3])
  * Alternative (more advanced): use **SSSD’s proxy provider** and point it at a local helper that resolves identities from your cache; you still get SSSD’s caching and offline behavior. ([Red Hat Docs][4])
  * If you prefer a prebuilt sync tool, Google’s **`nsscache`** can populate NSS backends from a remote directory—use your sync as the “remote” or adapt it. ([GitHub][5])

**System bits to put in place**

* `nsswitch.conf` (example):
  `passwd: files extrausers`
  `group:  files extrausers`
  `shadow: files extrausers`
* A **systemd service + timer** that:

  * Uses the service account (JWT/OAuth) to call Directory API.
  * Paginates with `pageToken` and writes changed records to the cache and then to `/var/lib/extrausers/`.
  * Applies deterministic **UID/GID allocation** (e.g., fixed range + stable hashing or a registry table) so UIDs don’t drift across hosts.
  * Writes **sudoers fragments** in `/etc/sudoers.d/` from designated Google Groups (e.g., “linux-admins”).
* **Home directory creation**: add `pam_mkhomedir.so` to PAM (or use systemd-homed) so first login creates `/home/$user`.
* **Authorized keys** (if you use SSH keys): either store keys in your cache and implement an `AuthorizedKeysCommand` script that reads from it, or adopt SSH certificates (below).

### Authentication

# Quota & reliability considerations

* Your sync runs on a **timer** (e.g., every 5–10 minutes) and only calls **Directory API**—no per-login API calls—so logins won’t hit Google quotas.
* Use pagination (`pageToken`) correctly and restrict fields to what you need. ([Google for Developers][2])
* With DWD, keep scopes minimal and rotate keys as policy dictates. ([Google Help][7])
* SSSD (if you use its proxy flow) gives you offline logins for a TTL; with `extrausers`, NSS is just local files, so it’s naturally resilient. ([Pagure Documentation][8])

## Director setup tips (rsync/SSH)

Create a non-privileged account (e.g., idcache) on each director with read access to /srv/idcache/extrausers.tgz*.

Install a read-only SSH key on agents; restrict it on directors with authorized_keys options, e.g.:


# Minimal build sheet (what you actually install/configure)

* **Google side**

  * Admin SDK / Directory API enabled; Service Account with **Domain-Wide Delegation** and read-only scopes for users/groups. ([Google Cloud][1])

* **Linux side**

  * `libnss-extrausers` (or SSSD + proxy provider). ([Unix & Linux Stack Exchange][3])
  * A small **sync daemon** (Python/Go):

    * Auth: service account JWT flow with DWD.
    * Calls `users.list`, `groups.list`, `members.list`.
    * Maintains `/var/lib/extrausers/{passwd,group,shadow}` and optional `/etc/sudoers.d/google-groups-*`. ([Google for Developers][2])
  * **PAM**:

    * For OIDC: install `pam-keycloak-oidc` (or another PAM OIDC module) and point it at your IdP that federates to Google. ([GitHub][6])
    * Or configure **SSH certs** (no Directory API calls on login).
  * **`pam_mkhomedir.so`** to auto-create home directories on first login.
  * Optional: `AuthorizedKeysCommand` script to read SSH keys from your cache (if you don’t go the SSH-cert route).


[1]: https://cloud.google.com/chronicle/docs/soar/marketplace-integrations/google-workspace?utm_source=chatgpt.com "Integrate Google Workspace with Google SecOps"
[2]: https://developers.google.com/workspace/admin/directory/reference/rest/v1/users/list?utm_source=chatgpt.com "Method: users.list | Admin console"
[3]: https://unix.stackexchange.com/questions/479832/what-is-the-purpose-of-extrausers?utm_source=chatgpt.com "What is the purpose of extrausers?"
[4]: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/7/html/system-level_authentication_guide/configuring_domains?utm_source=chatgpt.com "7.3. Configuring Identity and Authentication Providers for ..."
[5]: https://github.com/google/nsscache?utm_source=chatgpt.com "google/nsscache: asynchronously synchronise local NSS ..."
[6]: https://github.com/zhaow-de/pam-keycloak-oidc?utm_source=chatgpt.com "zhaow-de/pam-keycloak-oidc"
[7]: https://support.google.com/a/answer/162106?hl=en&utm_source=chatgpt.com "Control API access with domain-wide delegation"
[8]: https://docs.pagure.org/sssd.sssd/developers/internals.html?utm_source=chatgpt.com "SSSD Internals — SSSD documentation"
