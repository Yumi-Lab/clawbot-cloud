#!/usr/bin/env bash
#### ClawbotCloud — One-shot install script for Debian 13
#### Run as root on a fresh server:
####   curl -fsSL https://raw.githubusercontent.com/Yumi-Lab/clawbot-cloud/main/install.sh | bash
#### Or: bash install.sh after git clone

set -euo pipefail

REPO="https://github.com/Yumi-Lab/clawbot-cloud.git"
INSTALL_DIR="/opt/clawbot-cloud"
DATA_DIR="/var/lib/clawbot-cloud"
SERVICE_USER="clawbot"

echo "==> Installing ClawbotCloud..."

# ── 1. System packages ────────────────────────────────────────────────────────
apt-get update -q
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git nginx curl

# ── 2. Clone / update repo ────────────────────────────────────────────────────
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    echo "==> Updating existing install..."
    git -C "${INSTALL_DIR}" pull --ff-only
else
    git clone --depth=1 "${REPO}" "${INSTALL_DIR}"
fi

# ── 3. Python venv + dependencies ────────────────────────────────────────────
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"

# ── 4. Data directory ─────────────────────────────────────────────────────────
mkdir -p "${DATA_DIR}"

# ── 5. Service user ──────────────────────────────────────────────────────────
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}" "${INSTALL_DIR}"

# ── 6. .env file ─────────────────────────────────────────────────────────────
ENV_FILE="${INSTALL_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "==> Creating .env — fill in at least MOONSHOT_API_KEY"
    cat > "${ENV_FILE}" << EOF
DATABASE_URL=sqlite:///${DATA_DIR}/db.sqlite3
SECRET_KEY=${SECRET_KEY}
ADMIN_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(16))")

# LLM providers — at least one key required (Moonshot recommended)
MOONSHOT_API_KEY=sk-kimi-REPLACE_ME
DASHSCOPE_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
EOF
    chown "${SERVICE_USER}:${SERVICE_USER}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
fi

# ── 7. Systemd service ────────────────────────────────────────────────────────
cat > /etc/systemd/system/clawbot-cloud.service << EOF
[Unit]
Description=ClawbotCloud API
After=network.target

[Service]
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --quiet clawbot-cloud
systemctl restart clawbot-cloud

# ── 8. nginx ──────────────────────────────────────────────────────────────────
cat > /etc/nginx/sites-available/clawbot-cloud << 'EOF'
server {
    listen 80 default_server;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host            $host;
        proxy_set_header   X-Real-IP       $remote_addr;
        proxy_read_timeout 300s;
        proxy_buffering    off;
    }
}
EOF

ln -sf /etc/nginx/sites-available/clawbot-cloud /etc/nginx/sites-enabled/clawbot-cloud
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "✓ ClawbotCloud installed!"
echo ""
echo "  Edit your API keys:       nano ${ENV_FILE}"
echo "  Then restart:             systemctl restart clawbot-cloud"
echo ""
echo "  API running at:  http://$(curl -sf ifconfig.me 2>/dev/null || echo '<server-ip>')"
echo "  Health check:    curl http://localhost/health"
