# FR-voice-runtime-ci-publish — Automated PyPI Release Pipeline

**Status:** Draft
**Repo:** sheikkinen/voice_runtime
**Reference:** yamlgraph workflow.yml (trusted publisher pattern)

---

## Problem

`voice_runtime` is now a proper Python package (`pyproject.toml`, `LICENSE`,
public GitHub repo) but has no automated release pipeline. Publishing requires
manual `twine upload` with a stored API token, which:

- Ties publishing to a single developer's machine and credentials
- Provides no token rotation, audit trail, or CI gating
- Skips build verification on a clean environment

yamlgraph solves this with OIDC Trusted Publisher (no stored secrets) +
tag-triggered GitHub Actions. voice_runtime needs the same.

---

## Objective

Tag `v0.1.0` → GitHub Actions runs tests → builds wheel → publishes to PyPI
via OIDC trusted publisher → creates GitHub Release with release notes.

No PyPI API tokens stored anywhere.

---

## Implementation

### Step 1 — Register Trusted Publisher on PyPI

1. Log in to https://pypi.org
2. **Account Settings → Publishing → Add a new publisher**
3. Fill in:
   - PyPI project name: `voice-runtime`
   - GitHub owner: `sheikkinen`
   - GitHub repo: `voice_runtime`
   - Workflow filename: `publish.yml`
   - Environment name: `pypi`
4. Save — no token needed after this

> Do this **before** creating the workflow file. PyPI must know the publisher
> before the first push.

### Step 2 — Create GitHub Environment

In `sheikkinen/voice_runtime` → Settings → Environments → New environment:
- Name: `pypi`
- No secrets needed (OIDC handles auth)
- Optional: add required reviewers for extra gate

### Step 3 — Add workflow file

Create `.github/workflows/publish.yml` in the voice_runtime repo:

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - 'v*.*.*'

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read
  id-token: write

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.11', '3.12']
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Run tests
        run: pytest tests/ -q --no-cov

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip

      - name: Validate tag matches pyproject.toml version
        run: |
          VERSION="${GITHUB_REF#refs/tags/v}"
          PYPROJECT_VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
          if [ "$VERSION" != "$PYPROJECT_VERSION" ]; then
            echo "::error::Tag version ($VERSION) != pyproject.toml version ($PYPROJECT_VERSION)"
            exit 1
          fi

      - name: Install build tools
        run: pip install build

      - name: Build package
        run: python -m build

      - name: Upload dist artifacts
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/project/voice-runtime/
    permissions:
      id-token: write
    steps:
      - name: Download dist artifacts
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1

  create-release:
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
    needs: publish
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Download artifacts
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v1
        with:
          files: dist/*
          generate_release_notes: true
          draft: false
          prerelease: false
```

### Step 4 — Release flow (after pipeline is live)

```bash
cd projects/voice_runtime

# 1. Bump version in pyproject.toml
sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml

# 2. Update CHANGELOG.md

# 3. Commit
echo "chore(release): v0.1.1" > /tmp/msg.txt
git commit -F /tmp/msg.txt pyproject.toml CHANGELOG.md

# 4. Tag and push — CI does the rest
git tag v0.1.1
git push origin main
git push origin v0.1.1
```

---

## Acceptance Criteria

- [ ] Trusted publisher registered on PyPI for `voice-runtime` / `sheikkinen/voice_runtime` / `publish.yml` / env `pypi`
- [ ] GitHub environment `pypi` created in repo settings
- [ ] `.github/workflows/publish.yml` committed to `main`
- [ ] Push of tag `v0.1.0` triggers workflow
- [ ] `test` job: pytest passes on Python 3.11 and 3.12
- [ ] `build` job: tag version matches `pyproject.toml` version; wheel and sdist produced
- [ ] `publish` job: package appears at https://pypi.org/project/voice-runtime/
- [ ] `create-release` job: GitHub Release created with wheel attached and auto-generated notes
- [ ] No PyPI API tokens stored in GitHub Secrets

---

## Why OIDC / Trusted Publisher (not API token)

yamlgraph uses the same pattern. Benefits:

- **No secret to rotate or leak** — GitHub proves identity to PyPI via short-lived OIDC token
- **Scoped to one workflow file** — another workflow in the same repo cannot publish
- **Auditable** — PyPI shows which workflow triggered each upload
- **Industry standard** — PyPA's recommended approach since 2023

---

## Notes

- `twilio` (core dep) pulls in several transitive deps; `pip install -e ".[dev]"` in CI is sufficient for tests since azure is optional
- `azure` extra tests skip automatically via `requires_azure` marker — no SDK install needed in CI
- The `elevenlabs` SDK is installed as part of the default `elevenlabs` extra but tests mock it; can add `[elevenlabs]` to CI install if real ElevenLabs tests are added later
