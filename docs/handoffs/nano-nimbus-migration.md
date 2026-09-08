# Migration Plan: EDKarlsson/locus → Nano-Nimbus/locus

Migrate the Locus repository from the personal `EDKarlsson` GitHub account to the
`Nano-Nimbus` GitHub organization. Every downstream dependency — PyPI publishing,
MCP registries, Docker images, K8s deployment — must be updated in the correct order
to avoid a broken publish pipeline.

**Target org:** `Nano-Nimbus`
**Target repo URL:** `https://github.com/Nano-Nimbus/locus`
**PyPI package name:** `locus-mcp` (unchanged)
**MCP registry name:** `io.github.Nano-Nimbus/locus`

---

## Overview

```
Phase 0 — Code changes (PR, merged before transfer)
Phase 1 — GitHub repository transfer (manual, UI)
Phase 2 — PyPI OIDC trusted publisher update (critical, do immediately after transfer)
Phase 3 — MCP registries (Official Registry, mcp.so)
Phase 4 — Docker / GHCR
Phase 5 — K8s deployment image reference
Phase 6 — Local environment and wiki
Phase 7 — Verify end-to-end
```

The order matters. Phase 2 (PyPI) is time-sensitive: any tag push after the transfer
will attempt OIDC publishing under the new org — it will fail until the PyPI trusted
publisher is updated.

---

## Phase 0 — Code changes (before transfer)

Open a PR with all reference updates. Merge this before executing Phase 1.

### Files to update

**`README.md`**
- Line 3: `<!-- mcp-name: io.github.EDKarlsson/locus -->` → `io.github.Nano-Nimbus/locus`
- Line 5: CI badge URL → `Nano-Nimbus/locus`
- Line 37: wiki link → `Nano-Nimbus`
- Line 173: wiki link → `Nano-Nimbus`

**`pyproject.toml`**
- All 5 `[project.urls]` entries: `EDKarlsson` → `Nano-Nimbus`

**`CONTRIBUTING.md`**
- Clone URL: `EDKarlsson/locus` → `Nano-Nimbus/locus`

**`SPECIFICATION.md`**
- GitHub URL reference

**`server.json`** (MCP Official Registry format)
- `"name"`: `io.github.EDKarlsson/locus` → `io.github.Nano-Nimbus/locus`
- `"url"`: repository URL
- `"version"`: currently `0.8.0` — update to `0.9.0`
- `"packages"[0].version`: `0.8.0` → `0.9.0`

**`.github/workflows/publish.yml`**
- Comment on line 31: update `owner=EDKarlsson` → `owner=Nano-Nimbus`
  (This is documentation only — the OIDC token's `repository` claim is derived
  automatically from `GITHUB_REPOSITORY` after the transfer)

**Files to leave unchanged** (historical references, not functional):
- `CHANGELOG.md` — historical entries; adding a migration note at the top is sufficient
- `docs/audits/2026-03-03-pr-review-report.md` — audit snapshot

### CHANGELOG entry

Add to the top of `CHANGELOG.md`:

```markdown
## Repository move — 2026-03-XX

Locus has moved to the Nano-Nimbus GitHub organization.

- New repo: https://github.com/Nano-Nimbus/locus
- PyPI package (`locus-mcp`) and MCP server name (`io.github.Nano-Nimbus/locus`) updated.
- All old GitHub URLs are redirected automatically by GitHub.
```

### Branch for code changes

```sh
git checkout -b chore/nano-nimbus-org-refs-YYYYMMDD
# ... make changes ...
git push -u origin chore/nano-nimbus-org-refs-YYYYMMDD
gh pr create --title "chore: update all references to Nano-Nimbus org"
```

---

## Phase 1 — GitHub repository transfer (manual)

> **Prerequisite:** Phase 0 PR is merged to main.

1. Go to: `https://github.com/EDKarlsson/locus/settings`
2. Scroll to **Danger Zone** → **Transfer ownership**
3. Enter `Nano-Nimbus` as the destination organization
4. Confirm by typing the repository name

**What GitHub does automatically:**
- Old URL `github.com/EDKarlsson/locus` redirects to `github.com/Nano-Nimbus/locus`
- All issues, PRs, releases, stars, and forks transfer
- CI Actions continue to run; `GITHUB_REPOSITORY` env var becomes `Nano-Nimbus/locus`
- OIDC tokens issued by GitHub Actions update their `repository` claim automatically

**What GitHub does NOT do:**
- Update PyPI trusted publisher configuration
- Update any external registry entries
- Update your local git remote

**Immediately after transfer — update local remote:**
```sh
git remote set-url origin https://github.com/Nano-Nimbus/locus.git
git remote -v  # verify
```

---

## Phase 2 — PyPI OIDC trusted publisher (critical, do immediately)

The publish workflow uses OIDC trusted publishing — no token stored in GitHub secrets.
The trusted publisher is bound to a specific owner/repo/workflow/environment combination.
After the transfer, the first tag push will fail until this is updated.

**Steps:**
1. Go to: `https://pypi.org/manage/project/locus-mcp/settings/publishing/`
2. Find the existing trusted publisher entry:
   - Owner: `EDKarlsson`
   - Repository: `locus`
   - Workflow: `publish.yml`
   - Environment: `uv`
3. Delete the old entry
4. Add new trusted publisher:
   - Owner: `Nano-Nimbus`
   - Repository: `locus`
   - Workflow: `publish.yml`
   - Environment: `uv`

**Verify** by pushing a test tag after the next version bump:
```sh
git tag v0.9.1-rc1 && git push origin v0.9.1-rc1
# Watch the Actions run — it should publish successfully
# Delete the test tag if it was just a verification run
```

**Note:** The `environment: uv` setting in the GitHub Actions workflow
(`publish.yml` line 10) must match the environment name in the PyPI config exactly.

---

## Phase 3 — MCP Official Registry

The server is registered as `io.github.EDKarlsson/locus`. After the transfer,
this identifier needs to be updated to `io.github.Nano-Nimbus/locus`.

**Approach A — Re-publish with new name (preferred):**
```sh
# Download mcp-publisher if not already available
# From: https://github.com/modelcontextprotocol/registry/releases
/path/to/mcp-publisher login github
/path/to/mcp-publisher publish  # reads server.json
```

`server.json` must have the new name before running this. The old entry
(`io.github.EDKarlsson/locus`) should be unpublished or deprecated if
the tool supports it.

**Approach B — Contact MCP Registry maintainers:**
If the CLI doesn't support renaming an existing entry, open an issue at
the Official MCP Registry to request the rename/transfer.

**mcp.so:**
```sh
npx mcp-index https://github.com/Nano-Nimbus/locus
```

**Glama.ai / PulseMCP:** Auto-ingest from Official Registry — no action needed
once the registry is updated.

**Smithery.ai:** Was skipped at original submission (Python stdio not supported).
No action needed.

---

## Phase 4 — Docker / GHCR

Current image: `ghcr.io/edkarlsson/locus-mcp:0.8.0` (built manually with podman).

After the transfer, new images should be pushed to `ghcr.io/nano-nimbus/locus-mcp`.

**Option A — Manual build (current approach, podman):**
```sh
# Build from Dockerfile (note: update ARG LOCUS_MCP_VERSION)
sudo podman build --build-arg LOCUS_MCP_VERSION=0.9.0 \
  -t ghcr.io/nano-nimbus/locus-mcp:0.9.0 .
sudo podman push ghcr.io/nano-nimbus/locus-mcp:0.9.0

# Also tag latest
sudo podman tag ghcr.io/nano-nimbus/locus-mcp:0.9.0 ghcr.io/nano-nimbus/locus-mcp:latest
sudo podman push ghcr.io/nano-nimbus/locus-mcp:latest
```

**Option B — Add automated Docker build workflow (recommended):**
Create `.github/workflows/docker.yml` to build and push to GHCR on every tag:

```yaml
name: Docker

on:
  push:
    tags: ["v*"]

jobs:
  docker:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Extract version from tag
        id: version
        run: echo "version=${GITHUB_REF_NAME#v}" >> $GITHUB_OUTPUT
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          push: true
          build-args: LOCUS_MCP_VERSION=${{ steps.version.outputs.version }}
          tags: |
            ghcr.io/nano-nimbus/locus-mcp:${{ steps.version.outputs.version }}
            ghcr.io/nano-nimbus/locus-mcp:latest
```

**Update `Dockerfile`:** The `ARG LOCUS_MCP_VERSION` default is currently `0.8.0` — update to `0.9.0`.

---

## Phase 5 — K8s deployment

The homelab K8s deployment uses the GHCR image. The manifest is in the
`homelab-iac` repository (separate repo, not in `locus/`).

**Find and update the image reference:**
```sh
# In homelab-iac repo
grep -r "edkarlsson/locus\|locus-mcp" . --include="*.yaml"
# Update image: ghcr.io/edkarlsson/locus-mcp:X.Y.Z
#           to: ghcr.io/nano-nimbus/locus-mcp:X.Y.Z
```

Deploy via the normal GitOps flow (commit, PR, Flux reconcile).

**Verify after rollout:**
```sh
KUBECONFIG=~/.kube/config-asgard kubectl --context=tailscale-operator.oryx-tegu.ts.net \
  -n locus get pods
# Should show Running with the new image
```

---

## Phase 6 — Local environment and wiki

**Local git remote** (if not already updated in Phase 1):
```sh
cd /home/alice/projects/locus
git remote set-url origin https://github.com/Nano-Nimbus/locus.git
git remote -v
```

**Wiki re-clone:**
```sh
cd /home/alice/projects
mv locus.wiki locus.wiki.old
git clone https://github.com/Nano-Nimbus/locus.wiki.git locus.wiki
```

**GitHub Project board:**
The project board at `https://github.com/users/EDKarlsson/projects/2` is user-scoped,
not repo-scoped. It will not transfer automatically. Options:
- Create a new project board under the `Nano-Nimbus` org
- Or continue using the personal project board (it's linked by URL, not embedded in code)

Update MEMORY.md if the project board URL changes:
```
GitHub Project board: https://github.com/orgs/Nano-Nimbus/projects/<new-number>
```

**`claude mcp` registration** (if registered via Claude Code CLI):
```sh
claude mcp remove locus
claude mcp add --scope user locus uvx -- locus-mcp --palace ~/.locus
# The command itself doesn't reference the org, so re-adding is optional
# unless the MCP server name in ~/.claude.json needs updating
```

---

## Phase 7 — End-to-end verification

Run through this checklist after all phases are complete:

```
[ ] git remote -v shows Nano-Nimbus/locus
[ ] git push origin main works without permission errors
[ ] https://github.com/Nano-Nimbus/locus loads correctly
[ ] Old URL https://github.com/EDKarlsson/locus redirects correctly
[ ] CI runs on a test PR (unit tests pass)
[ ] Tag a patch version (e.g. v0.9.1), confirm:
      [ ] PyPI publish workflow succeeds (OIDC auth works under new org)
      [ ] pypi.org/project/locus-mcp shows the new version
      [ ] Docker workflow (if added) pushes ghcr.io/nano-nimbus/locus-mcp:0.9.1
[ ] MCP Official Registry: io.github.Nano-Nimbus/locus resolves correctly
[ ] mcp.so shows updated repo URL
[ ] K8s pod is Running with new image
[ ] locus-mcp --palace ~/.locus starts without error
[ ] uv run pytest tests/unit/ passes (256 tests)
```

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| PyPI publish breaks between transfer and OIDC update | Medium | Do Phase 2 immediately after Phase 1; avoid pushing tags during the window |
| GitHub redirect expires | Low | GitHub permanent redirects last indefinitely for transfers; external links will work |
| MCP registry name conflict | Low | `io.github.Nano-Nimbus/locus` is a new name — no conflict expected |
| K8s deployment pulls missing image | Low | Old image at edkarlsson GHCR still exists; only new deploys break |
| Local clone pointing at old remote | Low | `git remote set-url` fixes immediately; pushes will 302-redirect in the meantime |
| GitHub Actions OIDC for Docker failing | Low | Ensure Nano-Nimbus org has `packages: write` permission in Actions settings |

---

## Estimated sequence

```
Day 0:  Phase 0 — open and merge code-changes PR
Day 0:  Phase 1 — transfer repo (5 min in GitHub UI)
Day 0:  Phase 2 — update PyPI OIDC (5 min on pypi.org) ← do within 1 hour of transfer
Day 1:  Phase 3 — update MCP registries
Day 1:  Phase 4 — build and push new Docker image; optionally add docker.yml workflow
Day 1:  Phase 5 — update K8s manifest in homelab-iac, Flux reconcile
Day 1:  Phase 6 — re-clone wiki, update project board if needed
Day 1:  Phase 7 — verification checklist
```
