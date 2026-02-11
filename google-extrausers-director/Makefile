# Makefile for google-extrausers-director (source install)
#
# Mirrors the install layout used by build.sh:
#   /usr/sbin/google-extrausers-director-sync.py
#   /usr/sbin/google-extrausers-director-publish
#   /etc/google-extrausers-director/config
#   /lib/systemd/system/google-extrausers-director-sync.service
#   /lib/systemd/system/google-extrausers-director-sync.timer
# plus ensures /srv/idcache exists
#
# Usage:
#   sudo make install
#   sudo make enable
#   sudo make disable
#   sudo make uninstall
#
# For packaging:
#   make DESTDIR=$(pwd)/pkgroot install

SHELL := /bin/bash

PREFIX      ?= /usr
SYSCONFDIR   ?= /etc
SYSTEMDUNITDIR ?= /lib/systemd/system

SBINDIR     := $(PREFIX)/sbin
CONFDIR     := $(SYSCONFDIR)/extrausers-director

# Payload files expected in the project root
SYNC_SCRIPT   := google-extrausers-director-sync.py
PUBLISH_SCRIPT:= google-extrausers-director-publish
CONFIG_FILE   := config
SERVICE_UNIT  := google-extrausers-director.service
TIMER_UNIT    := google-extrausers-director.timer

# Installed names
INST_SYNC     := $(SBINDIR)/google-extrausers-director-sync.py
INST_PUBLISH  := $(SBINDIR)/google-extrausers-director-publish
INST_CONFIG   := $(CONFDIR)/config
INST_SERVICE  := $(SYSTEMDUNITDIR)/google-extrausers-director-sync.service
INST_TIMER    := $(SYSTEMDUNITDIR)/google-extrausers-director-sync.timer

.PHONY: all install uninstall enable disable reload status check

all:
	@echo "Targets: install uninstall enable disable reload status"

check:
	@test -f "$(SYNC_SCRIPT)" || (echo "Missing $(SYNC_SCRIPT)" && exit 1)
	@test -f "$(PUBLISH_SCRIPT)" || (echo "Missing $(PUBLISH_SCRIPT)" && exit 1)
	@test -f "$(CONFIG_FILE)" || (echo "Missing $(CONFIG_FILE)" && exit 1)
	@test -f "$(SERVICE_UNIT)" || (echo "Missing $(SERVICE_UNIT)" && exit 1)
	@test -f "$(TIMER_UNIT)" || (echo "Missing $(TIMER_UNIT)" && exit 1)

install: check

	# Create directories
	install -d "$(DESTDIR)$(SBINDIR)"
	install -d "$(DESTDIR)$(CONFDIR)"
	install -d "$(DESTDIR)$(SYSTEMDUNITDIR)"

	# Install scripts
	install -m 0755 "$(SYNC_SCRIPT)"    "$(DESTDIR)$(INST_SYNC)"
	install -m 0755 "$(PUBLISH_SCRIPT)" "$(DESTDIR)$(INST_PUBLISH)"

	# Install config (template)
	install -m 0640 "$(CONFIG_FILE)"    "$(DESTDIR)$(INST_CONFIG)"

	# Install systemd units
	# Note: build.sh installs google-extrausers-director.service as google-extrausers-director-sync.service
	install -m 0644 "$(SERVICE_UNIT)"   "$(DESTDIR)$(INST_SERVICE)"
	install -m 0644 "$(TIMER_UNIT)"     "$(DESTDIR)$(INST_TIMER)"

	# Only touch systemd on a real host install (not when DESTDIR is used)
	@if [ -z "$(DESTDIR)" ] && command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload || true; \
	fi

enable:
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload || true; \
		systemctl enable --now google-extrausers-director-sync.timer; \
	else \
		echo "systemctl not found"; exit 1; \
	fi

disable:
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl disable --now google-extrausers-director-sync.timer || true; \
		systemctl disable --now google-extrausers-director-sync.service || true; \
		systemctl daemon-reload || true; \
	else \
		echo "systemctl not found"; exit 1; \
	fi

reload:
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload; \
	else \
		echo "systemctl not found"; exit 1; \
	fi

status:
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl status google-extrausers-director-sync.timer --no-pager || true; \
		systemctl status google-extrausers-director-sync.service --no-pager || true; \
	else \
		echo "systemctl not found"; exit 1; \
	fi

uninstall:
	# Stop/disable first if possible
	@if [ -z "$(DESTDIR)" ] && command -v systemctl >/dev/null 2>&1; then \
		systemctl disable --now google-extrausers-director-sync.timer || true; \
		systemctl disable --now google-extrausers-director-sync.service || true; \
	fi

	rm -f "$(DESTDIR)$(INST_SYNC)"
	rm -f "$(DESTDIR)$(INST_PUBLISH)"
	rm -f "$(DESTDIR)$(INST_SERVICE)"
	rm -f "$(DESTDIR)$(INST_TIMER)"
	# Keep config by default? Uncomment next line if you want full purge behavior.
	# rm -f "$(DESTDIR)$(INST_CONFIG)"
	# Optionally remove config dir if empty
	rmdir --ignore-fail-on-non-empty "$(DESTDIR)$(CONFDIR)" 2>/dev/null || true

	@if [ -z "$(DESTDIR)" ] && command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload || true; \
	fi

