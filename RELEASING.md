# Releasing CHAP

This document covers the one-time account setup, the secrets required, and
the per-release procedure. The actual publishes are automated; this guide
is about getting the automation wired up.

---

## Part 1: One-time account setup (do this once)

### 1.1 PyPI

1. Register a Brightbeam-owned account at <https://pypi.org/account/register/>.
   Username and email should be team-owned (not individual). Enable 2FA
   when prompted (TOTP app or hardware key).

2. Configure **Trusted Publishing** for each Python package. Go to
   <https://pypi.org/manage/account/publishing/> and add a pending
   publisher for each project:

   For `chap-coordinator`:
   ```
   PyPI Project Name: chap-coordinator
   Owner:             BrightbeamAI
   Repository name:   chap
   Workflow name:     release.yml
   Environment name:  pypi
   ```

   For `chap-langgraph`:
   ```
   PyPI Project Name: chap-langgraph
   Owner:             BrightbeamAI
   Repository name:   chap
   Workflow name:     release.yml
   Environment name:  pypi
   ```

   With pending publishers configured, the **first** publish via the
   GitHub workflow creates the PyPI project. No API token needed.

### 1.2 npm

1. Register a Brightbeam-owned account at <https://www.npmjs.com/signup>.
   Enable 2FA at <https://www.npmjs.com/settings/~/profile>. Use "Auth-only"
   mode initially; once everything is stable, switch to "Auth and writes".

2. Create the `@chap` organisation:
   ```bash
   npm login                  # log in as the Brightbeam account
   npm org create chap        # creates the @chap scope
   ```

   If `@chap` is already taken, fall back to `@brightbeam` and update the
   `name` field in every `packages/*/package.json` to use the new scope
   (`@brightbeam/coordinator`, etc.) plus the peer-dep refs.

3. Create an **automation token** at
   <https://www.npmjs.com/settings/~/tokens>:
   - Type: **Granular Access Token**
   - Permissions: **Publish**
   - Scope: `@chap` (or whichever scope you chose)
   - Expiration: 365 days (or shorter)

   Copy the token. You'll add it as a GitHub secret in the next step.

### 1.3 GitHub repository configuration

1. Add the npm token as a repo secret:
   ```
   Repository → Settings → Secrets and variables → Actions → New repository secret
   Name:  NPM_TOKEN
   Value: <the token from step 1.2.3>
   ```

2. Create a deployment environment for PyPI (gives you a manual approval
   gate if you want one, plus visibility in the Actions UI):
   ```
   Repository → Settings → Environments → New environment
   Name: pypi
   ```
   Optional: add "Required reviewers" to require a manual approval before
   PyPI publish runs.

3. (Optional but recommended) Enable branch protection on `main`:
   ```
   Repository → Settings → Branches → Add rule
   Branch name pattern: main
   Require status checks to pass before merging:
     - CI / TypeScript (Node 20)
     - CI / Python 3.12
     - CI / Cross-language conformance
   ```

That's the one-time setup done.

---

## Part 2: Cutting a release

Once the accounts and secrets are configured, a release is:

```bash
# 1. Make sure main is clean and CI is green.
git checkout main
git pull
gh run list --workflow=ci.yml --branch=main --limit=1   # or check GH UI

# 2. Bump versions in all the right places. See "Version bump checklist"
#    below. Open a PR with these changes, get CI green, merge.

# 3. After the PR is merged, tag the release.
git checkout main && git pull
git tag -a v0.2.6 -m "0.2.6: <one-line description>"
git push origin v0.2.6

# 4. The release.yml workflow runs automatically:
#    - test  (re-runs the full suite)
#    - publish-npm (publishes 3 packages in dependency order)
#    - publish-pypi-coordinator
#    - publish-pypi-langgraph
#    - github-release (creates the GitHub Release with notes)

# 5. Watch it run.
gh run watch
```

### Version bump checklist

When prepping the release PR, bump all of these together:

| File                                                       | What to change                                       |
| ---------------------------------------------------------- | ---------------------------------------------------- |
| `package.json` (root)                                      | `"version"` field                                    |
| `packages/coordinator/package.json`                        | `"version"`                                          |
| `packages/coordinator-mcp/package.json`                    | `"version"`, peer-dep ref to coordinator             |
| `packages/coordinator-a2a/package.json`                    | `"version"`, peer-dep refs                           |
| `packages/coordinator-py/pyproject.toml`                   | `version = "..."`                                    |
| `packages/coordinator-py/chap_coordinator/__init__.py`     | `__version__`                                        |
| `packages/chap-langgraph/pyproject.toml`                   | `version`, `chap-coordinator>=...`                   |
| `packages/chap-langgraph/chap_langgraph/__init__.py`       | `__version__`                                        |
| `reference/a2a-server-ts/package.json`                     | `"version"`                                          |
| `reference/a2a-server-ts/server.ts`                        | `version: "..."` in `makeChapAgentCard()` call       |
| `reference/a2a-server-py/server.py`                        | `version="..."` in `make_chap_agent_card()` call     |
| `packages/coordinator-a2a/src/card.ts`                     | default version in `makeChapAgentCard()`             |
| `.github/actions/chap-conformance/action.yml`              | `default: 'v...'` for the `ref` input                |
| `.github/actions/chap-conformance/README.md`               | `@v...` in the example                               |
| `CHANGELOG.md`                                             | Add a new entry at the top                           |
| `IMPLEMENTATIONS.md`                                       | Update version column in the table                   |

A simple search confirms nothing's missed:

```bash
# Should return nothing (excluding CHANGELOG.md and integrations/ which carry historical refs)
grep -rln "<old version>" --include="*.json" --include="*.toml" --include="*.py" \
  --include="*.ts" --include="*.yml" --include="*.md" \
  --exclude-dir=node_modules --exclude-dir=dist \
  --exclude=CHANGELOG.md \
  .
```

---

## Part 3: Troubleshooting

### "npm publish failed: 403 forbidden"

Most common causes:
1. The `NPM_TOKEN` secret is missing or expired. Regenerate at
   <https://www.npmjs.com/settings/~/tokens>.
2. The `@chap` scope is owned by someone else. `npm whoami` and `npm org ls chap`
   on a local machine to check.
3. Trying to republish a version that already exists. npm refuses
   re-publishing the same version even if you `npm unpublish` it (24-hour cooldown).
4. The token doesn't have publish permission on the scope. Tokens are
   scope-restricted; check the token's permissions in the npm UI.

### "PyPI publish failed: invalid OIDC claim"

The Trusted Publisher configuration on PyPI doesn't match the workflow
identity. Verify on <https://pypi.org/manage/project/chap-coordinator/settings/publishing/>:
- Repository owner is `BrightbeamAI`
- Repository name is `chap`
- Workflow file is `release.yml` (not `release.yaml`)
- Environment name is `pypi`

### "PyPI publish failed: project does not exist"

The Trusted Publisher was configured AFTER the first publish was
attempted. Either:
1. Add the pending publisher first (the recommended approach), or
2. Do one manual publish with an API token, then switch to Trusted
   Publishing for subsequent releases.

### "Workflow ran but nothing was published"

The publish jobs depend on `test` succeeding. Check the test job logs first.
The most common failure is the schema drift check: if you added a new
method to the TypeScript handler without updating the JSON catalogue, or
vice versa, the check fails.

### "Conformance harness failed in CI but passes locally"

The cross-language conformance job starts servers in the background and
waits 2-3 seconds for them to bind. If the runner is slow, increase the
sleep in `.github/workflows/ci.yml`.

---

## Part 4: First publish (chicken-and-egg)

The very first publish is special because the PyPI projects don't exist
yet. The PyPI Trusted Publishing "pending publisher" feature handles
this, but only if you configure it BEFORE the first publish:

1. Configure pending publishers on PyPI (Part 1.1 step 2)
2. Configure npm token on GitHub (Part 1.3 step 1)
3. Tag and push:
   ```bash
   git tag -a v0.2.5 -m "0.2.5: adoption release"
   git push origin v0.2.5
   ```
4. The first workflow run will:
   - Create `chap-coordinator` on PyPI from the pending publisher
   - Create `chap-langgraph` on PyPI from the pending publisher
   - Publish all 3 npm packages to `@chap`

Subsequent releases just need a version bump + tag.

---

## Part 5: Switching npm to OIDC trusted publishing (later)

Once everything is stable, you can drop the `NPM_TOKEN` secret entirely
in favour of OIDC trusted publishing. Setup:

1. On npm, go to each package's settings → "Trusted Publishers" →
   "Add GitHub Actions"
2. Configure:
   - Repository: `BrightbeamAI/chap`
   - Workflow: `release.yml`
   - Environment: (leave blank or set to a custom one)
3. In `release.yml`, remove the `NODE_AUTH_TOKEN` env var from the
   publish steps. The `id-token: write` permission is already set.

Refs:
- <https://docs.npmjs.com/trusted-publishers>
- <https://docs.pypi.org/trusted-publishers/>
