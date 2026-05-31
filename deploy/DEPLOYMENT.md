# Mithra AI — Deployment Guide

## Architecture
- **Frontend**: Vercel (auto-deploys from GitHub)
- **Backend API**: Hetzner VPS with Docker
- **Domain**: mithraai.in (GoDaddy)

---

## Step 1: GoDaddy DNS Setup

Add these DNS records in GoDaddy:

| Type | Name | Value |
|------|------|-------|
| A    | @    | `<vercel-ip>` (auto-configured by Vercel) |
| CNAME | www | cname.vercel-dns.com |
| A    | api  | `<your-hetzner-server-ip>` |

---

## Step 2: Frontend on Vercel

1. Go to [vercel.com](https://vercel.com) → New Project
2. Import `https://github.com/sri211/mithra-ai`
3. Set **Root Directory** to `frontend`
4. Set environment variable:
   - `NEXT_PUBLIC_API_URL` = `https://api.mithraai.in/api`
5. Add your domain `mithraai.in` in Project Settings → Domains

---

## Step 3: Backend on Hetzner

```bash
# SSH into your Hetzner server
ssh root@<YOUR_HETZNER_IP>

# Run the setup script
curl -fsSL https://raw.githubusercontent.com/sri211/mithra-ai/master/deploy/setup-hetzner.sh | bash

# Set your API key
nano /opt/mithraai/backend/.env
# Add: ANTHROPIC_API_KEY=your_key_here

# Start the backend
cd /opt/mithraai/backend
docker compose up -d

# Check it's running
curl http://localhost:8000/api/health
```

---

## Step 4: Verify

- Frontend: https://mithraai.in
- API health: https://api.mithraai.in/api/health
- API docs: https://api.mithraai.in/docs

---

## Updating the app

```bash
# On Hetzner server
cd /opt/mithraai
git pull origin master
cd backend
docker compose down && docker compose up -d --build
```

Frontend auto-deploys from GitHub via Vercel on every push to master.
