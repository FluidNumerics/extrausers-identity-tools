# Quickstart

## autogen-posix
The `autogen-posix` service is a Google Cloud Run Function (and associated infrastructre) that can be used to automatically populate `posixAccounts` fields for users in your Google Workspace organization. See the [autogen-posix README](./autogen-posix/README.md) for more details

## google-extrausers-director
The `google-extrausers-director` service uses Google's Admin SDK to pull user information for Google Workspace and Cloud Identity accounts; specifically, it leverages the `posixAccount` field obtained from a `user.get` call. To use this service, you need to [create a service account with domain wide delegation](https://support.google.com/a/answer/162106?hl=en) to impersonate an admin with `https://www.googleapis.com/auth/admin.directory.user.readonly` scopes.

### 1) Install dependencies (Debian/Ubuntu):
```
sudo apt-get update
sudo apt-get install -y python3 python3-google-api-python-client python3-google-auth python3-google-auth-httplib2 coreutils tar systemd libnss-extrausers
```

### 2) Install the package:
```
sudo dpkg -i extrausers-director_0.1.0-1_all.deb
```

### 3) Edit the config

```
sudoedit /etc/extrausers-director/config
# SA_KEY=/etc/google/sa.json
# IMPERSONATE=admin@yourdomain.com
# CUSTOMER=my_customer       # or set DOMAIN=example.org
# OUTDIR=/var/lib/extrausers
# DB=/var/lib/googleworkspace-idcache/users.db
# DEFAULT_SHELL=/bin/bash
# HOME_TEMPLATE=/home/{username}
# RPS=5
# MAX_RETRIES=5
# VERBOSE=1
```

Optionally, set the publish directory
```
sudoedit /etc/extrausers-director/publish.conf
# PUBLISH_DIR=/srv/idcache
```

### 4) Enable/start

```
sudo systemctl daemon-reload
sudo systemctl enable --now extrausers-director-sync.timer
# Immediate run + publish:
sudo systemctl start extrausers-director-sync.service
journalctl -u extrausers-director-sync.service -n 100 -f
```

### 5) Update nsswitch.conf

```
passwd: files extrausers
group: files extrausers
shadow: files extrausers
```