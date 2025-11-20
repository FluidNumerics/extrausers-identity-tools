#!/usr/bin/env bash
#
# Run with  podman run -it --rm -v $(pwd):/workspace:z debian:latest /workspace/build.sh
set -euo pipefail

apt-get update
apt-get install -y dpkg-dev coreutils tar

PKG="google-extrausers-director"
VER="0.0.0-1"
ARCH="all"

# Staging root for the package
STAGE="/workspace/build/${PKG}"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN"

# Install paths inside the .deb
mkdir -p \
  "$STAGE/usr/sbin" \
  "$STAGE/etc/google-extrausers-director" \
  "$STAGE/lib/systemd/system" \
  "$STAGE/usr/share/doc/${PKG}/examples"

# -------- control file --------
cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VER}
Section: admin
Priority: optional
Architecture: ${ARCH}
Maintainer: Fluid Numerics LLC <support@fluidnumerics.com>
Depends: python3, coreutils, tar, systemd, python3-googleapi, python3-google-auth, python3-google-auth-httplib2, python3-google-auth-oauthlib, libnss-extrausers
Description: Director service to sync Google Workspace users to /var/lib/extrausers and publish bundles
 Creates /var/lib/extrausers/passwd,group,shadow from Google Workspace / Cloud Identity,
 optionally provisioning missing posixAccounts. Also publishes an archive bundle for
 cluster nodes to pull.
EOF

# -------- postinst --------
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

mkdir -p /srv/idcache
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl enable --now google-extrausers-director-sync.timer || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

# -------- prerm --------
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
case "$1" in
  remove)
    if command -v systemctl >/dev/null 2>&1; then
      systemctl disable --now google-extrausers-director-sync.timer || true
    fi
    ;;
esac
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/prerm"

# -------- payload files --------
# Expect these to exist in the current directory
install -m0755 /workspace/google-extrausers-director-sync.py        "$STAGE/usr/sbin/google-extrausers-director-sync.py"
install -m0755 /workspace/google-extrausers-director-publish        "$STAGE/usr/sbin/google-extrausers-director-publish"

# Default config templates (admin will edit after install)
install -m0640 /workspace/config                    "$STAGE/etc/google-extrausers-director/config"
#install -m0644 director-publish.conf              "$STAGE/etc/extrausers-director/publish.conf"

# systemd units
install -m0644 /workspace/google-extrausers-director.service   "$STAGE/lib/systemd/system/google-extrausers-director-sync.service"
install -m0644 /workspace/google-extrausers-director.timer     "$STAGE/lib/systemd/system/google-extrausers-director-sync.timer"

# -------- build the .deb --------
OUT="/workspace/${PKG}_${VER}_${ARCH}.deb"
dpkg-deb --build "$STAGE" "$OUT"
echo "Built: $OUT"

