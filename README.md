# Google ExtraUsers

This is a lightweight, simple tool that uses the google admin sdk to populate the `/etc/extrausers/{passwd,group,shadow}` files. When combined with `nss_extrausers`, this provides a simple scaleable alternative to Google Secure LDAP that mitigates quota issues.

[**Quickstart**](./Quickstart.md)

## Components

The Google Directory API gives you **identity data** (users, groups), but **not password verification**. So you need two layers:

1. an **identity/NSS layer** to expose users & groups to Linux, and
2. an **auth/PAM layer** to actually authenticate people (Tailscale, OIDC/SAML or SSH-certs).


### Providing Identity (NSS) via a local sync cache and Google Admin SDK

To Create a Google Cloud project & service account and enable Admin SDK / Directory API. Grant domain-wide delegation (DWD) to the service account with the least scopes you need (e.g., `admin.directory.user.readonly`, `admin.directory.group.readonly`). ([Google Cloud][1])

The `google-extrausers-director` service pulls users & groups** with the Directory API (paginated `users.list`, `groups.list`, `members.list`). Users are mapped to Linux Posix accounts using the `PosixAccounts` field from a Workspace or Cloud Identity Premium `user` object.

After pulling and extracting relevant posix information, the `google-extrausers-director` service stores a local SQLite cache with `username` (POSIX), `uidNumber`, `gidNumber`, `gecos`, `home`, `shell`


Additionally, this service can write flat files files (e.g. for [**`libnss-extrausers`**](https://github.com/arkanelinux/libnss-extrausers) `/var/lib/extrausers/{passwd,group,shadow}`) . When `extrausers` is added to `nsswitch.conf` for the `passwd`, `group`, and `shadow` fields, identity resolution from Google Workspace accounts an be achieved.

### Authentication
This system is intended to be deployed on Slurm clusters where authentication to login nodes is provided via `tailscale` and authentication to compute nodes is managed through `pam_slurm_adopt`.
