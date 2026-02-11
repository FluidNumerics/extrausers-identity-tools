# Quickstart

## autogen-posix
The `autogen-posix` service is a Google Cloud Run Function (and associated infrastructure) that can be used to automatically populate `posixAccounts` fields for users in your Google Workspace organization. See the [autogen-posix README](./autogen-posix/README.md) for more details.

## google-extrausers-director

The `google-extrausers-director` service uses Google's Admin SDK to pull user information for Google Workspace and Cloud Identity accounts; specifically, it leverages the `posixAccount` field obtained from a `user.get` call. To use this service, you need to [create a service account with domain wide delegation](https://support.google.com/a/answer/162106?hl=en) to impersonate an admin with the following scopes:
* `https://www.googleapis.com/auth/admin.directory.user.readonly`
* `https://www.googleapis.com/auth/admin.directory.group.readonly`
* `https://www.googleapis.com/auth/admin.directory.group.member.readonly`

### Option A: Ansible deployment (recommended)

The ansible playbook handles all dependencies, nss_extrausers installation from source, nsswitch.conf setup, and service configuration on both **Ubuntu 22/24** and **Rocky Linux 9**.

See [ansible/README.md](./ansible/README.md) for full instructions.

```bash
cd ansible
# Edit inventory.ini and group_vars/directors.yml
ansible-playbook -i inventory.ini site.yml
```

### Option B: Manual installation

#### 1) Install dependencies

**Ubuntu 22.04 / 24.04:**
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-googleapi python3-google-auth \
  python3-google-auth-httplib2 python3-google-auth-oauthlib \
  git acl rsync gcc make autoconf automake libtool libc6-dev
```

**Rocky Linux 9:**
```bash
sudo yum install -y python3 python3-pip git acl rsync \
  gcc make autoconf automake libtool glibc-devel
sudo pip3 install google-api-python-client google-auth google-auth-httplib2 google-auth-oauthlib
```

#### 2) Build and install libnss-extrausers from source

```bash
git clone https://github.com/arkanelinux/libnss-extrausers.git /opt/libnss-extrausers
cd /opt/libnss-extrausers
autoreconf -fi
./configure --prefix=/usr --libdir=$(pkg-config --variable=libdir libc 2>/dev/null || echo /usr/lib64)
make -j$(nproc)
sudo make install
sudo ldconfig
```

Create the extrausers data directory:
```bash
sudo mkdir -p /var/lib/extrausers
sudo touch /var/lib/extrausers/{passwd,group,shadow}
```

#### 3) Install the director service

```bash
git clone https://github.com/FluidNumerics/google-extrausers.git /opt/google-extrausers
cd /opt/google-extrausers/google-extrausers-director
sudo make install
```

#### 4) Edit the config

```bash
sudoedit /etc/extrausers-director/config
# SA_KEY=/etc/google/sa.json
# IMPERSONATE=admin@yourdomain.com
# CUSTOMER=my_customer       # or set DOMAIN=example.org
# GROUP_START_GID=30000
# GROUP_END_GID=39999
# OUTDIR=/var/lib/extrausers
# DB=/var/lib/extrausers-director/idcache/google.db
# DEFAULT_SHELL=/bin/bash
# HOME_TEMPLATE=/home/{username}
# RPS=5
# MAX_RETRIES=5
# VERBOSE=1
```

Optionally, set the publish directory:
```bash
sudoedit /etc/extrausers-director/publish.conf
# PUBLISH_DIR=/srv/idcache
```

#### 5) Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now google-extrausers-director-sync.timer
# Immediate run + publish:
sudo systemctl start google-extrausers-director-sync.service
journalctl -u google-extrausers-director-sync.service -n 100 -f
```

#### 6) Update nsswitch.conf

Edit `/etc/nsswitch.conf` and add `extrausers` after `files`:

```
passwd:        files extrausers
group:         files extrausers
shadow:        files extrausers
```

## extrausers-agent
The `extrausers-agent` service is used to copy the `/srv/idcache/extrausers.tgz` artifact from director nodes to agent nodes and extract it to `/var/lib/extrausers/`.
