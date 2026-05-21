#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 host for SepsisAtlas.
#
# Run as root on the target machine:
#   ssh root@<host> bash -s < deploy/bootstrap.sh
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
  # NOTE: deliberately no broad NOPASSWD: ALL grant. prepare_dirs installs a
  # narrow rule limited to the exact commands deploy-main.sh needs (rsync,
  # install, chown, cp, systemctl reload caddy). If the deploy SSH key
  # leaks, the attacker can re-trigger a deploy but cannot escalate to
  # root or run arbitrary commands.
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

install_caddy() {
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  if [[ ! -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]]; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  fi
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' >/etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
  systemctl enable --now caddy
}

install_bun() {
  mkdir -p /opt/bun
  chown "$DEPLOY_USER:$DEPLOY_USER" /opt/bun
  if [[ ! -x /opt/bun/bin/bun ]]; then
    sudo -u "$DEPLOY_USER" bash -lc 'curl -fsSL https://bun.sh/install | BUN_INSTALL=/opt/bun bash'
  fi
  ln -sf /opt/bun/bin/bun /usr/local/bin/bun
  ln -sf /opt/bun/bin/bunx /usr/local/bin/bunx
}

prepare_dirs() {
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /opt/sepsisatlas
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /var/www
  install -d -o caddy -g caddy -m 755 /var/log/caddy
  install -d -o caddy -g caddy -m 750 /etc/caddy/conf.d

  # Narrow sudoers: exactly the commands deploy-main.sh runs, with literal
  # paths so wildcards can't be abused to read/write other files. This is
  # the ONLY sudo grant the deploy user has — there is no broad NOPASSWD
  # rule elsewhere.
  cat >/etc/sudoers.d/deploy-caddy <<'EOF'
deploy ALL=(root) NOPASSWD: \
    /usr/bin/install -d -o caddy -g caddy /var/www/atlas-main, \
    /usr/bin/rsync -a --delete /opt/sepsisatlas/main/web/dist/ /var/www/atlas-main/, \
    /usr/bin/chown -R caddy\:caddy /var/www/atlas-main, \
    /usr/bin/cp /opt/sepsisatlas/main/deploy/Caddyfile /etc/caddy/Caddyfile, \
    /usr/bin/systemctl reload caddy
EOF
  chmod 440 /etc/sudoers.d/deploy-caddy
  visudo -cf /etc/sudoers.d/deploy-caddy >/dev/null
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
  install_caddy
  install_bun
  prepare_dirs
  set_hostname_and_tz
  # SSH hardening LAST so any earlier failure doesn't lock us out before
  # we've verified the deploy user works.
  harden_ssh
  echo "bootstrap complete"
}

main "$@"
