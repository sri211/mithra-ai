#!/bin/bash
# Run this on your Hetzner server to set up the backend
# Usage: ssh root@<server-ip> 'bash -s' < deploy/setup-hetzner.sh

set -e

echo "=== Installing Docker ==="
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

echo "=== Cloning repo ==="
git clone https://github.com/sri211/mithra-ai.git /opt/mithraai
cd /opt/mithraai/backend

echo "=== Creating .env from template ==="
cp .env.example .env
echo ""
echo "IMPORTANT: Edit /opt/mithraai/backend/.env and set your ANTHROPIC_API_KEY"
echo "Then run: docker compose up -d"

echo "=== Installing Nginx ==="
apt-get install -y nginx certbot python3-certbot-nginx
cp /opt/mithraai/deploy/nginx.conf /etc/nginx/sites-available/mithraai
ln -sf /etc/nginx/sites-available/mithraai /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "=== SSL Certificate ==="
certbot --nginx -d api.mithraai.in --non-interactive --agree-tos -m srinathreddy.ksr@gmail.com

echo "Done! Start the backend with: cd /opt/mithraai/backend && docker compose up -d"
