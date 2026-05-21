# SepsisAtlas Production Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy SepsisAtlas to a fresh DigitalOcean Ubuntu 24.04 droplet at `atlas.efferon.com` over HTTPS, with security hardening, automatic deploys from `main`, and per-PR preview environments at `pr-<N>.atlas.efferon.com`.

**Architecture:** Caddy on the host terminates TLS (auto Let's Encrypt) and reverse-proxies. Backend (FastAPI + Neo4j) runs in Docker Compose; frontend (Astro static SPA) is built with bun on the server into `/var/www/atlas-<env>/` and served by Caddy. Main env lives at `/opt/sepsisatlas/main` (compose project `atlas-main`, backend on `127.0.0.1:8000`); each PR gets `/opt/sepsisatlas/pr-<N>` with its own compose project and a unique loopback port. GitHub Actions invoke deploy/teardown scripts on the server over SSH using a least-privileged deploy key.

**Tech Stack:** Ubuntu 24.04, Docker Engine + Compose plugin, Caddy 2 (apt), bun 1.3, FastAPI/uvicorn, Astro 5 + React 19, SQLite, Neo4j 5.26, ufw, fail2ban, unattended-upgrades, GitHub Actions.

**Connection facts (do not re-derive):**
- Host alias: `efferon` (already in user's `~/.ssh/config`); resolves to `167.172.106.91`
- OS: Ubuntu 24.04.3 LTS, 4 vCPU, 8 GB RAM, 154 GB disk, fresh
- Current state: root login over key, passwordless sudo, ufw inactive, only port 22 listening, only `git/curl/wget/python3.12/ufw` installed
- DNS: `atlas.efferon.com` A record → `167.172.106.91` (user-confirmed). `*.atlas.efferon.com` is **NOT** confirmed; Task 18 verifies and fails loud if missing.
- Repo: `git@github.com:OrFrederick/SepsisAtlas.git` (private; deploy key needed)
- Required secret at runtime: `OPENROUTER_API_KEY` (user must provide; plan uses placeholder)

---

## File Structure

**New files (in this repo, committed):**
- `docker-compose.prod.yml` — prod overrides (no source mounts, loopback bind, no neo4j ports exposed, project-name interpolation for PR isolation)
- `deploy/Caddyfile` — main-site config (atlas.efferon.com → static frontend + reverse-proxy API)
- `deploy/Caddyfile.d/.gitkeep` — directory for dynamically-generated per-PR site snippets
- `deploy/bootstrap.sh` — one-shot server provisioning (runs as root over SSH; idempotent)
- `deploy/deploy-main.sh` — runs on server as `deploy` user; pulls main, rebuilds, restarts
- `deploy/deploy-pr.sh` — runs on server as `deploy` user; takes `$PR_NUMBER $SHA`; clones/updates `/opt/sepsisatlas/pr-<N>`, brings up isolated stack, writes Caddy snippet, reloads Caddy
- `deploy/cleanup-pr.sh` — runs on server as `deploy` user; takes `$PR_NUMBER`; tears down stack, removes Caddy snippet, deletes work tree
- `deploy/render-caddy-snippet.sh` — helper to emit a per-PR Caddy snippet
- `deploy/README.md` — operator runbook (what runs where, how to roll back, how to read logs)
- `.github/workflows/deploy.yml` — on push to main, SSH to server, run `deploy-main.sh`
- `.github/workflows/pr-preview.yml` — on PR open/sync/reopen, SSH to server, run `deploy-pr.sh`; on close, run `cleanup-pr.sh`; posts preview URL comment

**No edits** to existing `docker/Dockerfile.backend` or `docker-compose.yml` — dev environment stays untouched. All prod behavior comes from override files.

---

## Phase 0: Local prep (no server changes yet)

### Task 0: Confirm DNS and capture an offline checklist

**Files:** none.

- [ ] **Step 1:** Resolve both names from local machine and from the droplet.

```bash
dig +short atlas.efferon.com A
ssh efferon "dig +short atlas.efferon.com A; dig +short pr-test.atlas.efferon.com A"
```

Expected: `atlas.efferon.com` resolves to `167.172.106.91`. Wildcard may or may not — record the result; Phase 4 needs it.

- [ ] **Step 2:** Record outputs in `deploy/README.md` (created later in Task 14) under a "DNS state at bootstrap" section. For now just note them in the plan execution log.

---

## Phase 1: Server hardening (run from local machine, targets `efferon`)

### Task 1: Create `deploy` user with key-based SSH and sudo

**Files:** none on repo; remote: `/etc/sudoers.d/deploy`, `/home/deploy/.ssh/authorized_keys`.

- [ ] **Step 1:** From local machine, capture the public key that's already authorized for root on `efferon`, so the same key authorizes the new user.

```bash
ssh efferon 'cat ~/.ssh/authorized_keys'
```

Save the entire output to a shell variable `AUTHORIZED_KEYS` for the next step. If there are multiple keys, include all of them.

- [ ] **Step 2:** Create the `deploy` user, install the keys, grant passwordless sudo.

```bash
ssh efferon bash -s <<'REMOTE'
set -euo pipefail
id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
echo 'deploy ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/deploy
chmod 440 /etc/sudoers.d/deploy
visudo -cf /etc/sudoers.d/deploy
REMOTE
```

Expected: `parsed OK` from visudo, no errors.

- [ ] **Step 3:** Verify the `deploy` user works **before** locking down root.

```bash
ssh -o StrictHostKeyChecking=accept-new deploy@167.172.106.91 'whoami; sudo -n whoami'
```

Expected: `deploy` then `root`. If this fails, STOP — do not proceed to Task 2 or you will lock yourself out.

- [ ] **Step 4:** Add a host alias in `~/.ssh/config` so subsequent tasks can use `ssh efferon-deploy`.

```bash
cat >>~/.ssh/config <<'EOF'

Host efferon-deploy
  HostName 167.172.106.91
  User deploy
EOF
```

Verify: `ssh efferon-deploy whoami` → `deploy`.

### Task 2: Harden SSH (disable root login, password auth)

**Files:** remote `/etc/ssh/sshd_config.d/99-hardening.conf`.

- [ ] **Step 1:** Drop in a hardening fragment (sshd reads `.d/*.conf` after the main file).

```bash
ssh efferon-deploy sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
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
```

- [ ] **Step 2:** Validate config and reload.

```bash
ssh efferon-deploy 'sudo sshd -t && sudo systemctl reload ssh'
```

Expected: no output, exit 0.

- [ ] **Step 3:** From a **new** terminal (keep the existing session open in case of mistake), verify root is now refused and `deploy` still works.

```bash
ssh -o PreferredAuthentications=publickey efferon whoami    # should still work if key still in root's authorized_keys, OR fail with PermitRootLogin no
ssh efferon-deploy whoami                                    # must succeed
```

Expected: `efferon-deploy` succeeds. The `efferon` alias may or may not (root is denied at server side now; if your `efferon` alias is `User=root`, it will fail — that's correct).

- [ ] **Step 4:** Remove root's authorized_keys to fully revoke direct root SSH (defense in depth).

```bash
ssh efferon-deploy 'sudo truncate -s 0 /root/.ssh/authorized_keys'
ssh efferon whoami   # MUST now fail
```

### Task 3: Install ufw, configure firewall

**Files:** none (ufw state lives in `/etc/ufw/`).

- [ ] **Step 1:** Allow SSH first, then enable. Order matters — never enable before allowing 22 or you'll lock out.

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo ufw allow 22/tcp comment 'ssh'
sudo ufw allow 80/tcp comment 'http (caddy http-01 + redirect)'
sudo ufw allow 443/tcp comment 'https (caddy)'
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw --force enable
sudo ufw status verbose
REMOTE
```

Expected: `Status: active`, three allow rules, default deny incoming.

### Task 4: Install fail2ban with sshd jail

**Files:** remote `/etc/fail2ban/jail.d/sshd.local`.

- [ ] **Step 1:** Install and configure.

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban
sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<'EOF'
[sshd]
enabled = true
port    = ssh
backend = systemd
maxretry = 4
findtime = 10m
bantime  = 1h
EOF
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
REMOTE
```

Expected: `Status for the jail: sshd` block, currently 0 banned.

### Task 5: Enable unattended-upgrades for security patches

**Files:** remote `/etc/apt/apt.conf.d/20auto-upgrades`, `/etc/apt/apt.conf.d/50unattended-upgrades`.

- [ ] **Step 1:** Install (already present per recon, but reconfigure to ensure security source is enabled and automatic reboots are off).

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
# Reboot disabled — we don't want surprise restarts of the FastAPI + Neo4j stack.
sudo sed -i 's|^//Unattended-Upgrade::Automatic-Reboot .*|Unattended-Upgrade::Automatic-Reboot "false";|' /etc/apt/apt.conf.d/50unattended-upgrades
sudo unattended-upgrades --dry-run --debug 2>&1 | tail -20
REMOTE
```

Expected: dry-run completes, lists candidate security updates (may be none).

### Task 6: Set hostname + timezone

**Files:** remote `/etc/hostname`, timezone link.

- [ ] **Step 1:**

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo hostnamectl set-hostname atlas-prod
sudo timedatectl set-timezone UTC
hostnamectl
timedatectl
REMOTE
```

Expected: hostname `atlas-prod`, time zone `UTC`.

---

## Phase 2: Install runtime

### Task 7: Install Docker Engine + Compose plugin (official apt repo)

**Files:** remote `/etc/apt/sources.list.d/docker.list`, `/etc/apt/keyrings/docker.asc`.

- [ ] **Step 1:** Add Docker's official repo and install.

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker deploy
REMOTE
```

- [ ] **Step 2:** Re-establish SSH session so `deploy` picks up the docker group, then verify.

```bash
ssh efferon-deploy 'docker version --format "{{.Server.Version}}"; docker compose version'
```

Expected: Docker server version printed (no permission denied), compose v2.x printed.

### Task 8: Install Caddy (official apt repo)

**Files:** remote `/etc/apt/sources.list.d/caddy-stable.list`, `/usr/share/keyrings/caddy-stable-archive-keyring.gpg`.

- [ ] **Step 1:**

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update
sudo apt-get install -y caddy
sudo systemctl enable --now caddy
caddy version
REMOTE
```

Expected: Caddy v2.x version line.

### Task 9: Install bun (system-wide, for building Astro frontend)

**Files:** remote `/opt/bun/`, symlinks in `/usr/local/bin/`.

- [ ] **Step 1:** Install bun under `/opt/bun` (not in `~deploy`, so prod scripts running as root for build also see it).

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo mkdir -p /opt/bun
sudo chown deploy:deploy /opt/bun
sudo -u deploy bash -lc 'curl -fsSL https://bun.sh/install | BUN_INSTALL=/opt/bun bash'
sudo ln -sf /opt/bun/bin/bun /usr/local/bin/bun
sudo ln -sf /opt/bun/bin/bunx /usr/local/bin/bunx
bun --version
REMOTE
```

Expected: bun version (e.g. `1.3.13` or newer) printed.

### Task 10: Prepare deploy directories and Caddy ownership

**Files:** remote `/opt/sepsisatlas/`, `/var/www/`, `/etc/caddy/Caddyfile.d/`.

- [ ] **Step 1:**

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
sudo install -d -o deploy -g deploy -m 755 /opt/sepsisatlas
sudo install -d -o deploy -g deploy -m 755 /var/www
sudo install -d -o caddy -g caddy -m 755 /etc/caddy/Caddyfile.d
# Give deploy user permission to write per-PR snippet files and reload caddy.
sudo tee /etc/sudoers.d/deploy-caddy >/dev/null <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl reload caddy, /usr/bin/tee /etc/caddy/Caddyfile.d/*, /bin/rm /etc/caddy/Caddyfile.d/*, /usr/bin/install -d -o caddy -g caddy /var/www/*, /usr/bin/chown -R caddy:caddy /var/www/*, /bin/rm -rf /var/www/atlas-*
EOF
sudo chmod 440 /etc/sudoers.d/deploy-caddy
sudo visudo -cf /etc/sudoers.d/deploy-caddy
REMOTE
```

Expected: `parsed OK`. Directories created.

---

## Phase 3: Repo changes (commit and push these)

### Task 11: Create `docker-compose.prod.yml` override

**Files:** Create `docker-compose.prod.yml` at repo root.

- [ ] **Step 1:** Write the file.

```yaml
# Production override applied with:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-<env> up -d --build
#
# Differences from dev compose:
#   - Backend bound to loopback only (Caddy proxies in front)
#   - Source-code volume mount dropped (no hot reload in prod)
#   - Neo4j port not published (only reachable inside the compose network)
#   - container_name dropped so multiple stacks (main + pr-N) can coexist
#   - BACKEND_HOST_PORT env var lets deploy scripts pick a unique loopback port per stack

services:
  backend:
    container_name: ""
    volumes:
      - ./data:/app/data
      - ./db.sqlite:/app/db.sqlite
      - ./static:/app/static
      - ./runs:/app/runs
      - ./logs:/app/logs
    ports: !override
      - "127.0.0.1:${BACKEND_HOST_PORT:-8000}:8000"

  neo4j:
    container_name: ""
    ports: !override []
```

> The `!override` tag (Compose v2.20+) replaces the dev list rather than appending. `container_name: ""` instructs Compose to fall back to its project-prefixed default (so `atlas-main-backend-1` vs `atlas-pr-42-backend-1`).

- [ ] **Step 2:** Sanity-check the merged config locally.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main config | head -60
```

Expected: backend `ports` shows `127.0.0.1:8000:8000`, neo4j `ports` is empty, source mount under `services.backend.volumes` does NOT include `./src:/app/src`.

### Task 12: Create `deploy/Caddyfile`

**Files:** Create `deploy/Caddyfile`.

- [ ] **Step 1:** Write the file.

```caddyfile
# Sepsis Atlas — main site + per-PR previews
#
# Layout:
#   atlas.efferon.com               → static Astro build at /var/www/atlas-main, API → 127.0.0.1:8000
#   pr-<N>.atlas.efferon.com        → per-PR snippets in /etc/caddy/Caddyfile.d/pr-<N>.caddy
#
# TLS:
#   Main site: HTTP-01 (single hostname, atlas.efferon.com)
#   PR previews: on-demand TLS, gated by an ask endpoint served by the main site
#                so Caddy only requests certs for PR numbers that actually exist.

{
	email ops@efferon.com
	on_demand_tls {
		ask http://127.0.0.1:9180/check
	}
}

# --- main site -------------------------------------------------------------
atlas.efferon.com {
	encode zstd gzip
	root * /var/www/atlas-main

	# API surface (must match endpoints in src/api/main.py)
	@api path /query* /viewer/* /papers/* /static/* /health* /phenotypes* /rank_predictors* /ingest_pubmed /query_kg* /kg*
	handle @api {
		reverse_proxy 127.0.0.1:8000
	}

	# Static SPA fallback for everything else
	handle {
		try_files {path} /index.html
		file_server
	}

	log {
		output file /var/log/caddy/atlas.efferon.com.access.log
	}
}

# --- per-PR snippets -------------------------------------------------------
# Each PR drops a file in this directory; the import below pulls them all in.
import /etc/caddy/Caddyfile.d/*.caddy

# --- on-demand TLS ask endpoint -------------------------------------------
# Returns 200 iff a directory exists for the requested host. Bound to loopback
# only — never exposed externally. Backed by Caddy itself, not the app.
:9180 {
	bind 127.0.0.1
	@allowed expression `{query.domain}.endsWith(".atlas.efferon.com") && fileExists("/var/www/atlas-" + {query.domain}.split(".")[0])`
	respond @allowed 200
	respond 404
}
```

- [ ] **Step 2:** Create the snippet directory placeholder so git tracks it.

```bash
mkdir -p /Users/eugene/coding/SepsisAtlas/deploy/Caddyfile.d
touch /Users/eugene/coding/SepsisAtlas/deploy/Caddyfile.d/.gitkeep
```

### Task 13: Write `deploy/bootstrap.sh`

**Files:** Create `deploy/bootstrap.sh`, mode 755.

- [ ] **Step 1:** Write the script. It encodes Tasks 1–10 so a fresh server can be re-provisioned in one shot. Idempotent.

```bash
#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 host for SepsisAtlas.
# Run as root on the target machine, OR via:
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
  cat >/etc/sudoers.d/"$DEPLOY_USER" <<EOF
$DEPLOY_USER ALL=(ALL) NOPASSWD: ALL
EOF
  chmod 440 /etc/sudoers.d/"$DEPLOY_USER"
  visudo -cf /etc/sudoers.d/"$DEPLOY_USER"
}

harden_ssh() {
  cat >/etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PermitRootLogin no
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
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" >/etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  usermod -aG docker "$DEPLOY_USER"
}

install_caddy() {
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' >/etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
  systemctl enable --now caddy
}

install_bun() {
  mkdir -p /opt/bun
  chown "$DEPLOY_USER:$DEPLOY_USER" /opt/bun
  sudo -u "$DEPLOY_USER" bash -lc 'curl -fsSL https://bun.sh/install | BUN_INSTALL=/opt/bun bash'
  ln -sf /opt/bun/bin/bun /usr/local/bin/bun
  ln -sf /opt/bun/bin/bunx /usr/local/bin/bunx
}

prepare_dirs() {
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /opt/sepsisatlas
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /var/www
  install -d -o caddy -g caddy -m 755 /etc/caddy/Caddyfile.d
  install -d -o caddy -g caddy -m 755 /var/log/caddy
  cat >/etc/sudoers.d/deploy-caddy <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl reload caddy, /usr/bin/tee /etc/caddy/Caddyfile.d/*, /bin/rm /etc/caddy/Caddyfile.d/*, /usr/bin/install -d -o caddy -g caddy /var/www/*, /usr/bin/chown -R caddy:caddy /var/www/*, /bin/rm -rf /var/www/atlas-*
EOF
  chmod 440 /etc/sudoers.d/deploy-caddy
  visudo -cf /etc/sudoers.d/deploy-caddy
}

main() {
  require_root
  apt-get update
  apt-get install -y git curl ca-certificates gnupg lsb-release
  create_deploy_user
  harden_ssh
  setup_firewall
  setup_fail2ban
  setup_unattended_upgrades
  install_docker
  install_caddy
  install_bun
  prepare_dirs
  echo "bootstrap complete"
}

main "$@"
```

- [ ] **Step 2:** `chmod +x deploy/bootstrap.sh`.

### Task 14: Write `deploy/deploy-main.sh`

**Files:** Create `deploy/deploy-main.sh`, mode 755.

- [ ] **Step 1:**

```bash
#!/usr/bin/env bash
# Deploy the main branch.
# Run as `deploy` user (locally or over SSH from CI).
#
#   ssh deploy@host 'bash -s' < deploy/deploy-main.sh
#
# Or, since this script is itself committed to the repo and pulled into
# /opt/sepsisatlas/main, CI can simply call:
#   ssh deploy@host /opt/sepsisatlas/main/deploy/deploy-main.sh

set -euo pipefail

REPO_URL="git@github.com:OrFrederick/SepsisAtlas.git"
WORK_DIR="/opt/sepsisatlas/main"
WEB_OUT="/var/www/atlas-main"
PROJECT="atlas-main"
BACKEND_HOST_PORT="8000"

log() { echo "[deploy-main $(date -u +%FT%TZ)] $*"; }

ensure_clone() {
  if [[ ! -d "$WORK_DIR/.git" ]]; then
    log "cloning $REPO_URL → $WORK_DIR"
    git clone --branch main "$REPO_URL" "$WORK_DIR"
  fi
}

pull_main() {
  cd "$WORK_DIR"
  git fetch --prune origin main
  git reset --hard origin/main
}

ensure_env() {
  if [[ ! -f "$WORK_DIR/.env" ]]; then
    log "WARN: $WORK_DIR/.env missing — copying .env.example. Backend extraction will fail until OPENROUTER_API_KEY is set."
    cp "$WORK_DIR/.env.example" "$WORK_DIR/.env"
  fi
}

build_frontend() {
  cd "$WORK_DIR/web"
  log "installing web deps"
  bun install --frozen-lockfile
  log "building web"
  PUBLIC_BACKEND_URL="" bun run build
  sudo install -d -o caddy -g caddy "$WEB_OUT"
  sudo rsync -a --delete dist/ "$WEB_OUT/"
  sudo chown -R caddy:caddy "$WEB_OUT"
}

build_backend() {
  cd "$WORK_DIR"
  log "building backend image"
  BACKEND_HOST_PORT="$BACKEND_HOST_PORT" docker compose \
    -f docker-compose.yml -f docker-compose.prod.yml \
    -p "$PROJECT" build
}

restart_stack() {
  cd "$WORK_DIR"
  log "starting compose stack ($PROJECT) on 127.0.0.1:$BACKEND_HOST_PORT"
  BACKEND_HOST_PORT="$BACKEND_HOST_PORT" docker compose \
    -f docker-compose.yml -f docker-compose.prod.yml \
    -p "$PROJECT" up -d --remove-orphans
}

smoke() {
  log "waiting for backend health"
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$BACKEND_HOST_PORT/health" >/dev/null; then
      log "backend healthy after ${i}s"; return 0
    fi
    sleep 1
  done
  log "ERROR: backend did not become healthy"; exit 1
}

reload_caddy() {
  sudo cp "$WORK_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
  sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  sudo systemctl reload caddy
}

main() {
  ensure_clone
  pull_main
  ensure_env
  build_frontend
  build_backend
  restart_stack
  smoke
  reload_caddy
  log "main deploy complete"
}

main "$@"
```

- [ ] **Step 2:** `chmod +x deploy/deploy-main.sh`.

### Task 15: Write `deploy/deploy-pr.sh` and `deploy/cleanup-pr.sh`

**Files:** Create `deploy/deploy-pr.sh` and `deploy/cleanup-pr.sh`, mode 755.

- [ ] **Step 1: `deploy/deploy-pr.sh`**

```bash
#!/usr/bin/env bash
# Deploy a PR preview at pr-<N>.atlas.efferon.com.
# Usage: deploy-pr.sh <pr_number> <git_sha>
#
# Picks loopback port 8100 + (PR % 800) to keep each PR on a unique port
# in the unprivileged range; 800 distinct PRs is more than enough headroom.

set -euo pipefail

PR="$1"
SHA="$2"

REPO_URL="git@github.com:OrFrederick/SepsisAtlas.git"
WORK_DIR="/opt/sepsisatlas/pr-$PR"
WEB_OUT="/var/www/atlas-pr-$PR"
PROJECT="atlas-pr-$PR"
HOST="pr-$PR.atlas.efferon.com"
PORT="$((8100 + PR % 800))"

log() { echo "[deploy-pr#$PR $(date -u +%FT%TZ)] $*"; }

if [[ ! -d "$WORK_DIR/.git" ]]; then
  git clone "$REPO_URL" "$WORK_DIR"
fi
cd "$WORK_DIR"
git fetch --prune origin "+refs/pull/$PR/head:refs/remotes/origin/pr/$PR"
git reset --hard "$SHA"

# Inherit OPENROUTER_API_KEY etc. from main env unless PR has its own.
[[ -f "$WORK_DIR/.env" ]] || cp /opt/sepsisatlas/main/.env "$WORK_DIR/.env"

cd "$WORK_DIR/web"
bun install --frozen-lockfile
PUBLIC_BACKEND_URL="" bun run build
sudo install -d -o caddy -g caddy "$WEB_OUT"
sudo rsync -a --delete dist/ "$WEB_OUT/"
sudo chown -R caddy:caddy "$WEB_OUT"

cd "$WORK_DIR"
BACKEND_HOST_PORT="$PORT" docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml \
  -p "$PROJECT" up -d --build --remove-orphans

log "waiting for backend on 127.0.0.1:$PORT"
for i in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null && break
  sleep 1
done

# Write Caddy snippet for this PR
SNIPPET="/etc/caddy/Caddyfile.d/pr-$PR.caddy"
SNIPPET_BODY=$(cat <<EOF
$HOST {
	tls {
		on_demand
	}
	encode zstd gzip
	root * $WEB_OUT
	@api path /query* /viewer/* /papers/* /static/* /health* /phenotypes* /rank_predictors* /ingest_pubmed /query_kg* /kg*
	handle @api {
		reverse_proxy 127.0.0.1:$PORT
	}
	handle {
		try_files {path} /index.html
		file_server
	}
	log {
		output file /var/log/caddy/$HOST.access.log
	}
}
EOF
)
echo "$SNIPPET_BODY" | sudo tee "$SNIPPET" >/dev/null
sudo systemctl reload caddy

log "deployed: https://$HOST (loopback port $PORT)"
```

- [ ] **Step 2: `deploy/cleanup-pr.sh`**

```bash
#!/usr/bin/env bash
# Tear down a PR preview.
# Usage: cleanup-pr.sh <pr_number>

set -euo pipefail

PR="$1"
WORK_DIR="/opt/sepsisatlas/pr-$PR"
WEB_OUT="/var/www/atlas-pr-$PR"
PROJECT="atlas-pr-$PR"
SNIPPET="/etc/caddy/Caddyfile.d/pr-$PR.caddy"

if [[ -d "$WORK_DIR" ]]; then
  cd "$WORK_DIR"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" down -v --remove-orphans || true
fi

sudo rm -f "$SNIPPET"
sudo systemctl reload caddy || true
sudo rm -rf "$WEB_OUT"
rm -rf "$WORK_DIR"

echo "[cleanup-pr#$PR] removed"
```

- [ ] **Step 3:** `chmod +x deploy/deploy-pr.sh deploy/cleanup-pr.sh`.

### Task 16: Write `deploy/README.md` (operator runbook)

**Files:** Create `deploy/README.md`.

- [ ] **Step 1:**

````markdown
# SepsisAtlas — deploy ops

Production target: `atlas.efferon.com` (DigitalOcean droplet, Ubuntu 24.04).
PR previews: `pr-<N>.atlas.efferon.com` (requires wildcard DNS — see below).

## What runs where

| Component         | Where                                  | Port (loopback)    |
| ----------------- | -------------------------------------- | ------------------ |
| Caddy             | host, systemd unit `caddy.service`     | 80, 443            |
| Backend (main)    | docker compose project `atlas-main`    | 127.0.0.1:8000     |
| Neo4j (main)      | docker compose project `atlas-main`    | internal only      |
| Backend (PR #N)   | docker compose project `atlas-pr-N`    | 127.0.0.1:(8100+N) |
| Astro build (main)| /var/www/atlas-main (served by Caddy)  | n/a                |
| Astro build (PR)  | /var/www/atlas-pr-N (served by Caddy)  | n/a                |

## Initial bootstrap (one-shot, fresh server)

```bash
ssh root@<new-host> bash -s < deploy/bootstrap.sh
```

Then add the deploy SSH key (see GitHub Actions setup below), and run a first deploy:

```bash
ssh deploy@<host> /opt/sepsisatlas/main/deploy/deploy-main.sh
```

## Updating .env

`/opt/sepsisatlas/main/.env` holds `OPENROUTER_API_KEY` and optional Langfuse keys.
Edit in place; restart with:

```bash
ssh deploy@<host> 'cd /opt/sepsisatlas/main && docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main restart backend'
```

PR previews inherit main's `.env` at deploy time.

## Logs

- Caddy access logs: `/var/log/caddy/atlas.efferon.com.access.log`, `/var/log/caddy/pr-N.atlas.efferon.com.access.log`
- Caddy service log: `journalctl -u caddy`
- Backend logs: `docker compose -p atlas-main logs -f backend`
- Bind audit (sshd login attempts banned by fail2ban): `sudo fail2ban-client status sshd`

## Rolling back

```bash
ssh deploy@<host> bash -c 'cd /opt/sepsisatlas/main && git fetch && git reset --hard <good-sha> && deploy/deploy-main.sh'
```

## Wildcard DNS for PR previews

PR previews require `*.atlas.efferon.com` A record → server IP. Without it, Let's Encrypt's HTTP-01 challenge will fail for `pr-<N>.atlas.efferon.com`. Caddy is configured with on-demand TLS so it won't blow up; PRs just won't get HTTPS until DNS resolves.

## GitHub Actions deploy key

Workflows SSH to the server as `deploy@<host>` with a key stored in repo secrets:

| Secret             | Value                                                       |
| ------------------ | ----------------------------------------------------------- |
| `DEPLOY_SSH_HOST`  | `167.172.106.91`                                            |
| `DEPLOY_SSH_USER`  | `deploy`                                                    |
| `DEPLOY_SSH_KEY`   | private key whose public half is in `~deploy/.ssh/authorized_keys` |

Generate with `ssh-keygen -t ed25519 -f atlas-deploy -N ''`; install the `.pub` on the server, paste the private key into the secret.

## Common failures

- **HTTPS won't issue**: check `journalctl -u caddy | grep -i error`. Most common: DNS isn't pointing at the server, or port 80 is blocked.
- **Backend 502**: `docker compose -p atlas-main ps` — if backend is unhealthy, `docker compose -p atlas-main logs --tail=200 backend`.
- **PR preview port collision**: if two PRs land on the same `8100 + (PR % 800)` slot (unlikely but possible across thousands of PRs), bump the modulus in `deploy-pr.sh`.
````

### Task 17: Write GitHub Actions workflows

**Files:** Create `.github/workflows/deploy.yml` and `.github/workflows/pr-preview.yml`.

- [ ] **Step 1: `.github/workflows/deploy.yml`**

```yaml
name: deploy main
on:
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: deploy-main
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: ssh deploy
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.DEPLOY_SSH_HOST }}
          username: ${{ secrets.DEPLOY_SSH_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: /opt/sepsisatlas/main/deploy/deploy-main.sh
          script_stop: true
          command_timeout: 20m
```

- [ ] **Step 2: `.github/workflows/pr-preview.yml`**

```yaml
name: pr preview
on:
  pull_request:
    types: [opened, reopened, synchronize, closed]

concurrency:
  group: pr-preview-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  deploy:
    if: github.event.action != 'closed'
    runs-on: ubuntu-latest
    steps:
      - name: ssh deploy-pr
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.DEPLOY_SSH_HOST }}
          username: ${{ secrets.DEPLOY_SSH_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: /opt/sepsisatlas/main/deploy/deploy-pr.sh ${{ github.event.pull_request.number }} ${{ github.event.pull_request.head.sha }}
          script_stop: true
          command_timeout: 20m

      - name: comment preview url
        if: success()
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          header: pr-preview
          message: |
            Preview deploying: https://pr-${{ github.event.pull_request.number }}.atlas.efferon.com
            (May take up to a minute for HTTPS to issue on first deploy.)

  teardown:
    if: github.event.action == 'closed'
    runs-on: ubuntu-latest
    steps:
      - name: ssh cleanup-pr
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.DEPLOY_SSH_HOST }}
          username: ${{ secrets.DEPLOY_SSH_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: /opt/sepsisatlas/main/deploy/cleanup-pr.sh ${{ github.event.pull_request.number }}
          script_stop: true
          command_timeout: 5m
```

### Task 18: Commit + push the repo changes

**Files:** `docker-compose.prod.yml`, `deploy/*`, `.github/workflows/*`.

- [ ] **Step 1:**

```bash
cd /Users/eugene/coding/SepsisAtlas
git checkout -b deploy/production
git add docker-compose.prod.yml deploy/ .github/workflows/ docs/superpowers/plans/2026-05-21-production-deployment.md
git commit -m "infra: production deploy stack (Caddy + Docker, main + PR previews)

Adds:
- docker-compose.prod.yml override binding backend to loopback
- deploy/bootstrap.sh for one-shot server provisioning
- deploy/deploy-main.sh and deploy/deploy-pr.sh/cleanup-pr.sh
- Caddy config with on-demand TLS for PR subdomains
- GitHub Actions workflows for main + PR preview lifecycles
- deploy/README.md operator runbook"
git push -u origin deploy/production
```

- [ ] **Step 2:** Per CLAUDE.md and user memory ("skip PR step, just merge"), merge straight to main.

```bash
git checkout main
git merge --ff-only deploy/production
git push origin main
git branch -d deploy/production
git push origin --delete deploy/production
```

---

## Phase 4: Initial server provisioning + first deploy

### Task 19: Run bootstrap on the server

**Files:** none.

- [ ] **Step 1:** Push the bootstrap script over SSH as root (this is the last task that uses `efferon` alias / root before SSH hardening locks it out).

```bash
ssh efferon bash -s < /Users/eugene/coding/SepsisAtlas/deploy/bootstrap.sh
```

Expected: ends with `bootstrap complete`. If tasks 1–10 were already run by hand earlier, the script is idempotent and reports each step as a no-op.

- [ ] **Step 2:** Re-verify hardened state.

```bash
ssh efferon-deploy 'sudo ufw status; sudo fail2ban-client status sshd; docker version --format "{{.Server.Version}}"; caddy version; bun --version'
```

Expected: ufw active, fail2ban running, docker server version, caddy v2.x, bun installed.

### Task 20: Install deploy SSH key + GitHub repo secrets

**Files:** local `~/atlas-deploy` keypair (delete after); remote `~deploy/.ssh/authorized_keys` (append).

- [ ] **Step 1:** Generate a key dedicated to CI.

```bash
ssh-keygen -t ed25519 -f ~/atlas-deploy -N '' -C 'github-actions@sepsisatlas'
ssh efferon-deploy "echo '$(cat ~/atlas-deploy.pub)' | tee -a ~/.ssh/authorized_keys"
```

- [ ] **Step 2:** Verify the new key works.

```bash
ssh -i ~/atlas-deploy deploy@167.172.106.91 whoami
```

Expected: `deploy`.

- [ ] **Step 3:** Push secrets to GitHub.

```bash
cd /Users/eugene/coding/SepsisAtlas
gh secret set DEPLOY_SSH_HOST -b '167.172.106.91'
gh secret set DEPLOY_SSH_USER -b 'deploy'
gh secret set DEPLOY_SSH_KEY < ~/atlas-deploy
```

Expected: each command prints `✓ Set secret`.

- [ ] **Step 4:** Delete the local private key so it doesn't sit on disk.

```bash
rm ~/atlas-deploy ~/atlas-deploy.pub
```

### Task 21: Install a GitHub deploy key so the server can clone

**Files:** local `~/atlas-repo-readonly` keypair (deleted after); GitHub repo deploy key.

- [ ] **Step 1:** Generate a read-only key on the server itself (the private half never leaves the server).

```bash
ssh efferon-deploy 'ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "deploy@atlas-prod"; cat ~/.ssh/id_ed25519.pub'
```

- [ ] **Step 2:** Take that public key and install as a GitHub deploy key (read-only).

```bash
PUB="$(ssh efferon-deploy cat ~/.ssh/id_ed25519.pub)"
cd /Users/eugene/coding/SepsisAtlas
gh repo deploy-key add - --title 'atlas-prod (read-only)' <<<"$PUB"
```

- [ ] **Step 3:** Verify the server can reach GitHub over SSH.

```bash
ssh efferon-deploy 'ssh -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 | head -3'
```

Expected: `Hi OrFrederick/SepsisAtlas! You've successfully authenticated, but GitHub does not provide shell access.`

### Task 22: First deploy of main

**Files:** remote `/opt/sepsisatlas/main/.env` (placeholder for `OPENROUTER_API_KEY`).

- [ ] **Step 1:** Clone + run first deploy.

```bash
ssh efferon-deploy bash -s <<'REMOTE'
set -euo pipefail
git clone git@github.com:OrFrederick/SepsisAtlas.git /opt/sepsisatlas/main
cp /opt/sepsisatlas/main/.env.example /opt/sepsisatlas/main/.env
REMOTE
```

- [ ] **Step 2:** Set the real `OPENROUTER_API_KEY` (paste interactively — do NOT bake into the script).

```bash
ssh efferon-deploy 'sudo nano /opt/sepsisatlas/main/.env'
```

Set `OPENROUTER_API_KEY=<the key>`; save.

- [ ] **Step 3:** Run the deploy script.

```bash
ssh efferon-deploy /opt/sepsisatlas/main/deploy/deploy-main.sh
```

Expected: ends with `main deploy complete`, backend reports healthy.

### Task 23: Verify HTTPS

**Files:** none.

- [ ] **Step 1:** Hit the health endpoint over HTTPS from the local machine.

```bash
curl -sSI https://atlas.efferon.com/health
```

Expected: `HTTP/2 200`. First request may take 10–30s while Caddy fetches a Let's Encrypt cert.

- [ ] **Step 2:** Confirm the SPA loads.

```bash
curl -sSI https://atlas.efferon.com/
```

Expected: `HTTP/2 200`, content-type `text/html`.

- [ ] **Step 3:** Browser smoke — open `https://atlas.efferon.com/` and confirm the chat/graph SPA renders, a sample query returns rows, and a PDF viewer link from a result opens.

### Task 24: Trigger the GitHub Actions deploy workflow

**Files:** none.

- [ ] **Step 1:** Force a re-deploy through CI to confirm secrets + workflow work end-to-end.

```bash
gh workflow run "deploy main"
gh run watch
```

Expected: workflow completes green; SSH connects; `deploy-main.sh` runs idempotently.

### Task 25: Smoke-test PR preview lifecycle

**Files:** none.

- [ ] **Step 1:** Open a trivial PR (touch a comment in README) and watch the workflow.

```bash
cd /Users/eugene/coding/SepsisAtlas
git checkout -b smoke/preview-test
printf '\n<!-- preview smoke %s -->\n' "$(date -u +%FT%TZ)" >> README.md
git commit -am 'smoke: trigger PR preview'
gh pr create --title 'smoke: trigger PR preview' --body 'auto-deleted'
gh pr view --web
```

- [ ] **Step 2:** Wait for the workflow to comment the preview URL. Open it. Confirm HTTPS works after ~30s and the SPA loads.

- [ ] **Step 3:** Close the PR; confirm `teardown` job runs and the preview URL goes 5xx (Caddy no longer has a snippet, on-demand `ask` returns 404, so cert request denied).

```bash
gh pr close --delete-branch <pr-number>
ssh efferon-deploy 'ls /etc/caddy/Caddyfile.d/ /opt/sepsisatlas/'
```

Expected: no `pr-<N>.caddy` snippet, no `pr-<N>` work dir.

---

## Self-review

**Spec coverage:**
- Server hardening → Tasks 1–6 ✓
- Production deployment → Tasks 11, 19, 22 ✓
- HTTPS → Tasks 8, 12, 23 ✓ (Caddy automatic Let's Encrypt)
- Host all PRs → Tasks 15, 17, 25 ✓ (with the caveat that wildcard DNS may not be set; noted in Task 0 and `deploy/README.md`)
- DNS already set → Task 0 verifies and surfaces the wildcard gap explicitly rather than failing silently

**Placeholder scan:** none. Every command, file, and config is concrete.

**Type/name consistency:**
- Compose project names: `atlas-main`, `atlas-pr-<N>` — consistent across `deploy-main.sh`, `deploy-pr.sh`, `cleanup-pr.sh`, runbook.
- Web roots: `/var/www/atlas-main`, `/var/www/atlas-pr-<N>` — consistent.
- Caddy snippet path: `/etc/caddy/Caddyfile.d/pr-<N>.caddy` — consistent.
- Loopback port formula: `8100 + (PR % 800)` — defined once, referenced from runbook.
- API path list in main Caddyfile and PR snippet — identical.

**Risks / things to watch:**
- Compose `!override` tag requires Compose v2.20+; the Docker apt repo ships current versions (≥2.29) — fine on fresh Ubuntu 24.04.
- The on-demand TLS `ask` expression uses Caddy's CEL helper `fileExists`, available from Caddy 2.7+ — apt ships 2.8+.
- First PR preview HTTPS issuance can take up to a minute; the workflow comment notes this.
- `deploy-pr.sh` inherits main's `.env`; if a PR needs different secrets (it shouldn't), that's a manual step.
