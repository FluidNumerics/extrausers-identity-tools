# extrausers-identity-tools

A lightweight identity synchronization toolchain that bridges **Google Workspace / Cloud Identity** user and group accounts to **Linux POSIX identities** via [`libnss-extrausers`](https://github.com/arkanelinux/libnss-extrausers).

Designed for Slurm clusters and similar multi-node Linux environments where:
- Authentication to login nodes is handled externally (e.g. Tailscale SSH, OIDC, SSH certificates)
- Authentication to compute nodes is managed via `pam_slurm_adopt`
- User identity (UID/GID/home/shell) must be consistent across all nodes without LDAP

---

## How It Works

```
Google Workspace ──► google-extrausers-director ──► /var/lib/extrausers/{passwd,group,shadow}
                          (systemd timer)                         │
                               │                                  │
                          publishes tarball              libnss-extrausers
                          to /srv/idcache                         │
                               │                          nsswitch.conf
                        agent nodes pull ──────────────► getent passwd/group
```

The **director** periodically syncs users and groups from the Google Directory API and writes flat files consumed by `libnss-extrausers`. Agent (compute/login) nodes pull those files from the director.

No passwords are stored locally — all accounts are locked in shadow. Authentication is handled entirely by external mechanisms (SSH keys, Tailscale, PAM).

---

## Key Features

- **Google Workspace → POSIX user sync**: Reads `posixAccounts` fields from Workspace user objects; maps login name, UID, GID, home directory, and shell
- **Google Groups → POSIX group sync**: Derives stable GIDs deterministically from each group's Google `id` via SHA-256 hashing into a configured numeric range — no database required for consistency across director instances
- **Stable identities**: User/group POSIX IDs survive email renames; the Google object `id` is the stable key
- **Local SQLite cache**: Stores resolved POSIX attributes for consistent, offline-safe rendering
- **Locked shadow entries**: No local passwords; authentication is fully delegated to external systems
- **Multi-platform**: Ubuntu 22.04/24.04 and Rocky Linux 9
- **Ansible deployment**: One playbook handles all dependencies, builds `libnss-extrausers` from source, configures NSS, and enables the systemd timer

---

## Repository Structure

```
extrausers-identity-tools/
├── autogen-posix/               # Terraform + Cloud Run Function: auto-populate posixAccounts in Workspace
├── google-extrausers-director/  # Python sync service + systemd units + Makefile
└── ansible/                    # Ansible role to deploy the director on Ubuntu or Rocky Linux
```

### `autogen-posix`

An optional Google Cloud Run Function (triggered by Cloud Scheduler) that automatically populates the `posixAccounts` field for users in your Google Workspace organization. Use this if your users don't already have POSIX attributes set in Workspace.

Deployed via Terraform. See [`autogen-posix/README.md`](./autogen-posix/README.md).

### `google-extrausers-director`

The core sync service. Runs on a designated director node as a systemd timer, pulling users and groups from the Google Admin SDK and writing:

- `/var/lib/extrausers/passwd`
- `/var/lib/extrausers/shadow`
- `/var/lib/extrausers/group`

Also publishes a tarball (`/srv/idcache/extrausers.tgz`) for agent nodes to pull.

See [`google-extrausers-director/README.md`](./google-extrausers-director/README.md).

### `ansible`

An Ansible role that automates the full director setup:

1. Installs OS packages and Python Google API libraries
2. Builds and installs `libnss-extrausers` from source
3. Seeds extrausers files and updates `/etc/nsswitch.conf`
4. Clones this repo and runs `make install`
5. Templates the director config (including service account key)
6. Enables and starts the systemd timer

See [`ansible/README.md`](./ansible/README.md).

---

## Getting Started

### Prerequisites

- A **Google Workspace or Cloud Identity** organization
- A **Google Cloud project** with the Admin SDK / Directory API enabled
- A **service account** with [Domain-Wide Delegation (DWD)](https://support.google.com/a/answer/162106?hl=en) and the following OAuth scopes:
  - `https://www.googleapis.com/auth/admin.directory.user.readonly`
  - `https://www.googleapis.com/auth/admin.directory.group.readonly`
  - `https://www.googleapis.com/auth/admin.directory.group.member.readonly`
- Linux hosts running **Ubuntu 22.04/24.04** or **Rocky Linux 9**
- Users must have `posixAccounts` populated in Google Workspace (use `autogen-posix` if needed)

### Step 1 (optional): Populate posixAccounts with autogen-posix

If your Workspace users don't have `posixAccounts` fields set, deploy the `autogen-posix` Cloud Run Function to auto-populate them.

```bash
cd autogen-posix
cp sample.auto.tfvars fluid.auto.tfvars
# Edit fluid.auto.tfvars with your project_id, region, domain, etc.
terraform init
terraform plan -out=tfplan
terraform apply "tfplan"
```

Then [assign Domain-Wide Delegation](https://support.google.com/a/answer/162106?hl=en) to the service account listed in the `admin_sa_unique_id` output with scope `https://www.googleapis.com/auth/admin.directory.user`.

### Step 2: Deploy the director — Ansible (recommended)

```bash
cd ansible
# 1. Edit inventory.ini with your director host(s)
# 2. Edit group_vars/directors.yml with your service account path, domain, and GID range
ansible-playbook -i inventory.ini site.yml
```

Minimum required variables in `group_vars/directors.yml`:

```yaml
extrausers_sa_key_path: "/etc/google/sa.json"   # path to SA key JSON on the host
extrausers_impersonate: "admin@example.org"       # Workspace admin account to impersonate
extrausers_domain: "example.org"                  # domain to sync
extrausers_group_start_gid: 30000                 # GID range for Google Groups
extrausers_group_end_gid: 39999
```

See [`ansible/README.md`](./ansible/README.md) for the full variable reference.

### Step 2 (alternative): Manual installation

See the [Quickstart](./Quickstart.md) for step-by-step manual installation instructions covering:
- OS package installation
- Building `libnss-extrausers` from source
- Installing the director (`make install`)
- Editing the config
- Enabling the systemd timer
- Updating `nsswitch.conf`

### Step 3: Verify

```bash
# Check the sync timer is running
systemctl status google-extrausers-director-sync.timer

# Watch the first sync
journalctl -u google-extrausers-director-sync.service -n 100 -f

# Verify identity resolution
getent passwd    # should include Google Workspace users
getent group     # should include Google Groups
```

---

## Director Configuration Reference

Config file: `/etc/extrausers-director/config`

| Variable | Required | Default | Description |
|---|---|---|---|
| `SA_KEY` | Yes | — | Path to service account JSON key |
| `IMPERSONATE` | Yes | — | Workspace admin email to impersonate |
| `CUSTOMER` | One of | `my_customer` | Google customer ID |
| `DOMAIN` | One of | — | Domain to sync (alternative to `CUSTOMER`) |
| `GROUP_START_GID` | No | `80000` | Start of GID range for Google Groups |
| `GROUP_END_GID` | No | `89999` | End of GID range for Google Groups |
| `OUTDIR` | No | `/var/lib/extrausers` | Output dir for passwd/group/shadow |
| `DB` | No | `/var/lib/googleworkspace-idcache/users.db` | SQLite cache path |
| `DEFAULT_SHELL` | No | `/bin/bash` | Default shell if not set in Workspace |
| `HOME_TEMPLATE` | No | `/home/{username}` | Home directory pattern |
| `RPS` | No | `5` | API requests per second |
| `MAX_RETRIES` | No | `5` | Max retries on transient API errors |
| `VERBOSE` | No | — | Set to `1` for verbose logging |
| `PUBLISH_DIR` | No | `/srv/idcache` | Where to publish the tarball for agent nodes |
| `IDCACHE_USER` | No | `idcache` | Unix user granted read access to published files |

---

## License

See [LICENSE](./LICENSE).
