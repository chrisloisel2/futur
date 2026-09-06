# V2 Threat Model (Phase 0 first pass)

Static/config-level findings only — no live network probing was done against
any running instance this session. Update this file as each item is either
confirmed live-exploitable or closed.

## Confirmed findings (from repo config, this session)

### 1. MongoDB published on all interfaces, no auth configured
`docker-compose.yml`:
```yaml
mongodb:
  ports:
    - "0.0.0.0:27017:27017"
```
No `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` (or any auth
env var) is set for the `mongodb` service, and `.env.example` only lists
connection URIs with no credentials. Anyone who can reach port 27017 on the
host — including, on a cloud/VPS deployment, the public internet unless a
firewall sits in front of Docker — gets unauthenticated read/write on every
collection (`historical_ohlcv`, `market_intel`, `whale_data`, `proxy_db`,
etc.). This matches the master prompt's named defect. **Status: confirmed at
config level; live reachability not tested this session — needs a check
against both this machine and `qbee@100.127.59.114` (per prior-session
memory, that host has diverged from `main` and its actual running compose
config is unverified).**

Fix direction (not yet implemented): bind to `127.0.0.1` unless a documented
reason requires LAN/WAN exposure, add root credentials via `.env`-sourced
secrets, and put any genuinely-remote access behind the same
authenticated-API/allowlist path Phase 8 requires for exchange keys.

### 2. `command-center` bind-mounts the entire repo and is tunneled publicly
```yaml
command-center:
  ports:
    - "0.0.0.0:8899:80"
  volumes:
    - .:/app
  ...
ngrok:
  command: ["http", "command-center:80", "--url", "https://default.internal", ...]
```
The whole working tree (`.:/app`) — code, configs, whatever `data/`/`reports/`
hold — is mounted into a container that's both published on `0.0.0.0:8899`
and reachable through a public ngrok URL. Blast radius of a compromise in
`frontend_pipeline/` (an `UNVERIFIED`-classified directory, see
`MIGRATION.md`) is "the entire repo," not just the frontend's own code.
**Status: confirmed at config level; whether `frontend_pipeline/` has any
known vulnerable surface (auth on its routes, admin endpoints) not yet
audited this session.**

### 3. No dependency lockfile → supply-chain / reproducibility risk
No root `pyproject.toml`/`uv.lock`/`requirements.txt`. `launch.sh` will
`pip install -r requirements-api.txt` with no pinned versions once that file
is created, and 4 different interpreters are reachable on the dev machine
with different package sets already. Not an active exploit, but it means
"what actually runs in prod" is not reconstructible from the repo today.
**Status: confirmed. Fix direction: root `pyproject.toml` + `uv.lock`,
Phase 1.**

## Explicitly not yet done this session (do not assume clean)

- **Git history secret scan.** Only checked that `.env` is gitignored and
  that `.env.example` contains no live credentials (it doesn't — placeholder
  URIs only). Did **not** scan full git history for accidentally-committed
  keys/tokens in any of the 1734 tracked `.py` files or in `legacy/`. This is
  a real gap given `legacy/` alone has 1247 files and multiple generations of
  scraper/collector code that historically touched exchange APIs.
- **Live reachability testing** of Mongo/qdrant/command-center on this
  machine or on `qbee@100.127.59.114`.
- **Admin/API route auth audit** — the master prompt specifically flags
  "certaines routes d'administration peuvent être exposées sans
  authentification suffisante"; no route-level audit was performed this
  session, only the compose-file port/binding review above.
- **Exchange API key permission audit** (withdrawal-disabled requirement) —
  not checked; no key material was found or examined this session (correctly
  — keys should not be in the repo).

## Next action

Run a live-reachability check (`nc`/`curl` against 27017/6333/8899 from
outside `localhost`, and against `qbee@100.127.59.114`'s equivalent ports)
before doing anything else security-related, since "confirmed at config
level" is not the same claim as "confirmed exploitable in the current
deployment." Then do the git-history secret scan (e.g. `gitleaks` or
`trufflehog` over full history, not just HEAD).
