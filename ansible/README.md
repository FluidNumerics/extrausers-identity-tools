# Ansible Deployment

Deploy the `google-extrausers-director` service on **Ubuntu 22/24** or **Rocky Linux 9** using the included Ansible role.

## Prerequisites

On the control machine:
- Ansible 2.12+
- SSH access to target hosts (with sudo/become)

On target hosts:
- systemd
- Internet access (to clone repos and install packages)

A Google Cloud service account JSON key with Domain-Wide Delegation is required. See the [Quickstart](../Quickstart.md) for Google Cloud setup steps.

## What the role does

1. Installs OS packages (build tools, Python, git, acl)
2. Builds and installs **libnss-extrausers** from source (works on both Debian and RHEL families)
3. Seeds `/var/lib/extrausers/{passwd,group,shadow}` and configures `/etc/nsswitch.conf`
4. Clones this repository and runs `make install` for the director service
5. Templates the director config and optional service-account key
6. Enables and starts the systemd timer

## Quick start

### 1. Edit the inventory

```ini
# ansible/inventory.ini
[directors]
dir1 ansible_host=10.0.0.11
dir2 ansible_host=10.0.0.12
```

### 2. Set required variables

Copy and edit the group vars:

```yaml
# ansible/group_vars/directors.yml
extrausers_sa_key_path: "/etc/google/sa.json"
extrausers_impersonate: "admin@example.org"
extrausers_domain: "example.org"

extrausers_group_start_gid: 30000
extrausers_group_end_gid: 39999
```

To deploy the service-account key from Ansible Vault:

```yaml
extrausers_sa_json: "{{ vault_extrausers_sa_json }}"
```

### 3. Run the playbook

```bash
cd ansible
ansible-playbook -i inventory.ini site.yml
```

Or limit to a single host:

```bash
ansible-playbook -i inventory.ini site.yml --limit dir1
```

## Variables reference

### Global (`group_vars/all.yml`)

| Variable | Default | Description |
|---|---|---|
| `extrausers_repo_url` | `https://github.com/FluidNumerics/google-extrausers.git` | Director source repo |
| `extrausers_repo_version` | `main` | Git ref to deploy |
| `extrausers_repo_dir` | `/opt/google-extrausers` | Clone destination on host |
| `nss_extrausers_repo_url` | `https://github.com/arkanelinux/libnss-extrausers.git` | libnss-extrausers source |
| `nss_extrausers_repo_version` | `main` | Git ref for libnss-extrausers |
| `nss_extrausers_build_dir` | `/opt/libnss-extrausers` | Build directory on host |

### Director (`group_vars/directors.yml`)

| Variable | Default | Description |
|---|---|---|
| `extrausers_sa_key_path` | `/etc/google/sa.json` | Path to SA key on host |
| `extrausers_impersonate` | `admin@example.org` | Google Workspace admin to impersonate |
| `extrausers_domain` | `""` | Domain to sync (leave empty to use `extrausers_customer`) |
| `extrausers_customer` | `my_customer` | Customer ID (used when `domain` is empty) |
| `extrausers_group_start_gid` | `30000` | Start of POSIX GID range for groups |
| `extrausers_group_end_gid` | `39999` | End of POSIX GID range for groups |
| `extrausers_default_shell` | `/bin/bash` | Default login shell |
| `extrausers_home_template` | `/home/{username}` | Home directory pattern |
| `extrausers_outdir` | `/var/lib/extrausers` | Output directory for passwd/group/shadow |
| `extrausers_db` | `/var/lib/extrausers-director/idcache/google.db` | SQLite cache path |
| `extrausers_rps` | `5` | API requests per second |
| `extrausers_max_retries` | `5` | Max retries on API errors |
| `extrausers_verbose` | `false` | Enable verbose logging |
| `extrausers_publish_dir` | `/srv/idcache` | Where to publish the extrausers tarball |
| `extrausers_idcache_user` | `idcache` | Unix user granted read access to published files |
| `extrausers_sa_json` | *(undefined)* | Raw SA key JSON (from vault); written to `sa_key_path` |

## Platform notes

### Ubuntu 22.04 / 24.04

Python Google API libraries are installed from apt (`python3-googleapi`, etc.). `libnss-extrausers` is built from source for consistency across platforms.

### Rocky Linux 9

Python Google API libraries are installed via pip3 (not available in base repos). Build tools (`gcc`, `autoconf`, `libtool`, `glibc-devel`) are installed from yum.

### libnss-extrausers

The role builds libnss-extrausers from source on all platforms. The library path is set automatically:
- **Debian/Ubuntu**: `/usr/lib/x86_64-linux-gnu/`
- **RHEL/Rocky**: `/usr/lib64/`

After installation, `ldconfig` is run and `/etc/nsswitch.conf` is updated to add the `extrausers` source for `passwd`, `group`, and `shadow` lookups.

## Verifying the deployment

After the playbook completes:

```bash
# Check the timer is active
systemctl status google-extrausers-director-sync.timer

# Check the last sync run
journalctl -u google-extrausers-director-sync.service -n 50

# Verify NSS resolution
getent passwd    # should include Google Workspace users
getent group     # should include Google Groups
```
