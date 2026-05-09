# Installation & Setup

## Prerequisites

- Docker (single-container deployment), or Python 3.13 + uv (local dev)
- A GitHub account with admin access to the root repository
- (Optional) Owner or admin access to the GitHub organisation whose members should have access

---

## GitHub OAuth Setup

The dashboard uses GitHub OAuth for authentication. You need to register an OAuth App once before deploying.

### 1. Register a GitHub OAuth App

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**
   (or navigate to `https://github.com/settings/applications/new`)
2. Fill in the form:
   - **Application name**: anything recognisable, e.g. `Git Leaderboard`
   - **Homepage URL**: your deployment URL, e.g. `https://leaderboard.example.com`
     (use `http://localhost:8000` for local development)
   - **Authorization callback URL**:
     - Production: `https://leaderboard.example.com/auth/callback`
     - Local dev: `http://localhost:8000/auth/callback`
3. Click **Register application**
4. On the next page, note the **Client ID**
5. Click **Generate a new client secret** and note the **Client Secret** — it is only shown once

### 2. Organisation membership visibility (if using org-based access)

By default, GitHub only exposes org membership to the OAuth app if the member has made their membership **public**, or if the org has explicitly approved the app.

If your org has third-party application restrictions enabled (common in company orgs):

1. Go to **GitHub → Your Organisation → Settings → Third-party access → OAuth App policy**
2. Find your newly registered app and click **Grant access**

Alternatively, ask each user to make their org membership public:
**GitHub profile → Edit profile → Organisation → change visibility to Public**

If neither option is available, contact a GitHub org owner to approve the app.

### 3. Configure the application

Set the following environment variables (or add them to a `.env` file — see `.env.example`):

```bash
# GitHub OAuth
GITHUB_CLIENT_ID=your_client_id_here
GITHUB_CLIENT_SECRET=your_client_secret_here
GITHUB_CALLBACK_URL=http://localhost:8000/auth/callback   # adjust for production

# Organisation whose members are granted access (optional — omit to allow fork owners only)
GITHUB_ORG=your-org-name

# Platform API token — used for fork discovery (needs repo + read:org scopes)
GITHUB_TOKEN=ghp_...

# Session signing key — generate with: uv run python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace_with_a_strong_random_value
```

### 4. Who gets access

A user who logs in via GitHub is granted access if **either** condition is true:

- They are a member of the GitHub org named in `GITHUB_ORG`, **or**
- They are the owner of one of the tracked forks

If `GITHUB_ORG` is not set, only fork owners can log in.

---

## Local Development

```bash
# Clone and install dependencies
git clone <this repo>
cd slop-leaderboard
uv sync

# Copy and edit the example env file
cp .env.example .env
# → fill in GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_TOKEN, SECRET_KEY

# Run the dev server
uv run uvicorn app.main:app --reload --port 8000
```

The callback URL in your OAuth App must be set to `http://localhost:8000/auth/callback`.

---

## Docker Deployment

```bash
# Build the image
docker build -t slop-leaderboard .

# Run with persistent data and env config
docker run -d \
  --name leaderboard \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  slop-leaderboard
```

Update the **Authorization callback URL** in your GitHub OAuth App to match your production domain.

---

## Environment Variable Reference

| Variable | Required | Description |
|---|---|---|
| `GITHUB_CLIENT_ID` | Yes | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | Yes | OAuth App client secret |
| `GITHUB_CALLBACK_URL` | Yes | Must match the callback URL registered in the OAuth App |
| `GITHUB_TOKEN` | Yes | Personal access token for fork discovery (scopes: `repo`, `read:org`) |
| `GITHUB_ORG` | No | Org name — members are granted dashboard access |
| `SECRET_KEY` | Yes | Random string used to sign session cookies |
| `SYNC_INTERVAL_MINUTES` | No | How often forks are re-analysed (default: `60`) |
| `DATA_DIR` | No | Path for SQLite DB and cloned repos (default: `./data`) |
