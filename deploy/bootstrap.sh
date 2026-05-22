#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 host for SepsisAtlas.
#
# Run as root on the target machine:
#   ssh root@<host> bash -s < deploy/bootstrap.sh
#
# After this script runs, the host has:
#   - a `deploy` user with the ops team's SSH keys + docker group membership
#   - docker engine + compose plugin
#   - ufw allowing 22/80/443, fail2ban watching sshd, unattended-upgrades
#   - /opt/sepsisatlas/ ready for the compose files (fetched separately)
#   - /etc/sepsisatlas/caddy-conf.d/ ready for private caddy directives
#
# Idempotent — re-running on a configured host is safe.

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
AUTHORIZED_KEYS_FILE="/root/.ssh/authorized_keys"

require_root() { [[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }; }

create_deploy_user() {
  id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER"
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
  if [[ -s "$AUTHORIZED_KEYS_FILE" ]]; then
    cp "$AUTHORIZED_KEYS_FILE" "/home/$DEPLOY_USER/.ssh/authorized_keys"
    chown "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh/authorized_keys"
    chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
  fi
  # NOTE: deliberately no broad NOPASSWD: ALL grant. The deploy user runs
  # docker compose via docker-group membership (granted by install_docker),
  # not via sudo. Any prior narrow sudoers rule from earlier bootstrap
  # revisions is cleaned up in prepare_dirs.
  #
  # Clean up the broad grant a previous bootstrap revision wrote at
  # /etc/sudoers.d/$DEPLOY_USER. Without this, an earlier-provisioned host
  # keeps the broad NOPASSWD: ALL grant indefinitely.
  rm -f "/etc/sudoers.d/$DEPLOY_USER"

  # Pre-create the docker creds file so the Watchtower bind-mount lands on
  # a file, not a directory. (Docker creates a directory if the source path
  # doesn't exist at bind-mount time, which then breaks the next
  # `docker login`.)
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.docker"
  [[ -f "/home/$DEPLOY_USER/.docker/config.json" ]] || \
    install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /dev/null "/home/$DEPLOY_USER/.docker/config.json"
}

harden_ssh() {
  cat >/etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 30s
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
  sshd -t
  systemctl reload ssh
}

setup_firewall() {
  apt-get install -y ufw
  ufw allow 22/tcp comment 'ssh'
  ufw allow 80/tcp comment 'http'
  ufw allow 443/tcp comment 'https'
  ufw allow 443/udp comment 'http3'
  ufw default deny incoming
  ufw default allow outgoing
  yes | ufw enable || true
}

setup_fail2ban() {
  DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban
  cat >/etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
port    = ssh
backend = systemd
maxretry = 4
findtime = 10m
bantime  = 1h
EOF
  systemctl enable --now fail2ban
}

setup_unattended_upgrades() {
  DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
  sed -i 's|^//Unattended-Upgrade::Automatic-Reboot .*|Unattended-Upgrade::Automatic-Reboot "false";|' /etc/apt/apt.conf.d/50unattended-upgrades || true
}

install_docker() {
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" >/etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  usermod -aG docker "$DEPLOY_USER"
}

prepare_dirs() {
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /opt/sepsisatlas
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /etc/sepsisatlas/caddy-conf.d
  # The deploy user no longer needs any narrow sudo rules — host caddy is gone,
  # all infra changes go through docker compose, and the deploy user is in the
  # docker group (granted by install_docker above).
  rm -f /etc/sudoers.d/deploy-caddy
}

set_hostname_and_tz() {
  hostnamectl set-hostname atlas-prod
  timedatectl set-timezone UTC
}

main() {
  require_root
  apt-get update
  apt-get install -y git curl ca-certificates gnupg lsb-release rsync unzip
  create_deploy_user
  setup_firewall
  setup_fail2ban
  setup_unattended_upgrades
  install_docker
  prepare_dirs
  set_hostname_and_tz
  # SSH hardening LAST so any earlier failure doesn't lock us out before
  # we've verified the deploy user works.
  harden_ssh
  echo "bootstrap complete"
}

main "$@"
