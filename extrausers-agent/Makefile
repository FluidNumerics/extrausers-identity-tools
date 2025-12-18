# Makefile for extrausers-agent (rsync/ssh puller)
#
# Installs:
#   /usr/sbin/extrausers-agent-and-install
#   /etc/extrausers-agent/sources.list
#   /lib/systemd/system/extrausers-agent.service
#   /lib/systemd/system/extrausers-agent.timer
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

PREFIX          ?= /usr
SYSCONFDIR      ?= /etc
SYSTEMDUNITDIR  ?= /lib/systemd/system

SBINDIR   := $(PREFIX)/sbin

SCRIPT_SRC    ?= extrausers-agent-sync
SERVICE_SRC   ?= extrausers-agent.service
TIMER_SRC     ?= extrausers-agent.timer
SOURCES_SRC   ?= sources.list

SCRIPT_DST    := $(SBINDIR)/extrausers-agent-and-install
SERVICE_DST   := $(SYSTEMDUNITDIR)/extrausers-agent.service
TIMER_DST     := $(SYSTEMDUNITDIR)/extrausers-agent.timer

.PHONY: all check install uninstall enable disable reload status

all:
	@echo "Targets: install uninstall enable disable reload status"

check:
	@test -f "$(SCRIPT_SRC)"  || (echo "Missing $(SCRIPT_SRC)" && exit 1)
	@test -f "$(SERVICE_SRC)" || (echo "Missing $(SERVICE_SRC)" && exit 1)
	@test -f "$(TIMER_SRC)"   || (echo "Missing $(TIMER_SRC)" && exit 1)
	@test -f "$(SOURCES_SRC)" || (echo "Missing $(SOURCES_SRC)" && exit 1)

install: check
	# Create directories
	install -d "$(DESTDIR)$(SBINDIR)"
	install -d "$(DESTDIR)$(CONFDIR)"
	install -d "$(DESTDIR)$(SYSTEMDUNITDIR)"

	# Install script
	install -m 0755 "$(SCRIPT_SRC)" "$(DESTDIR)$(SCRIPT_DST)"

	# Install config template (admin will edit after install)
	install -m 0644 "$(SOURCES_SRC)" "$(DESTDIR)$(SOURCES_DST)"

	# Install systemd units
	install -m 0644 "$(SERVICE_SRC)" "$(DESTDIR)$(SERVICE_DST)"
	install -m 0644 "$(TIMER_SRC)"   "$(DESTDIR)$(TIMER_DST)"

	# Reload systemd on real host installs only
	@if [ -z "$(DESTDIR)" ] && command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload || true; \
	fi

enable:
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload || true; \
		systemctl enable --now extrausers-agent.timer; \
	else \
		echo "systemctl not found"; exit 1; \
	fi

disable:
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl disable --now extrausers-agent.timer || true; \
		systemctl disable --now extrausers-agent.service || true; \
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
		systemctl status extrausers-agent.timer --no-pager || true; \
		systemctl status extrausers-agent.service --no-pager || true; \
	else \
		echo "systemctl not found"; exit 1; \
	fi

uninstall:
	# Stop/disable first if possible (only on real host)
	@if [ -z "$(DESTDIR)" ] && command -v systemctl >/dev/null 2>&1; then \
		systemctl disable --now extrausers-agent.timer || true; \
		systemctl disable --now extrausers-agent.service || true; \
	fi

	rm -f "$(DESTDIR)$(SCRIPT_DST)"
	rm -f "$(DESTDIR)$(SERVICE_DST)"
	rm -f "$(DESTDIR)$(TIMER_DST)"

	# Keep sources.list by default (safer). Uncomment to purge.
	# rm -f "$(DESTDIR)$(SOURCES_DST)"
	# rmdir --ignore-fail-on-non-empty "$(DESTDIR)$(CONFDIR)" 2>/dev/null || true

	@if [ -z "$(DESTDIR)" ] && command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload || true; \
	fi

