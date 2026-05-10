# Publishing voice-runtime to PyPI

## Prerequisites

- Python 3.11+
- `pip install build twine` (both already installed if you ran `pip install -e ".[dev]"`)
- A PyPI account with a project token for `voice-runtime`
- All three parties have agreed to the MIT license and the release contents

---

## One-time setup

### 1. Create a PyPI API token

1. Log in to https://pypi.org
2. Account Settings → API tokens → Add API token
3. Scope: **Project** → `voice-runtime` (first time: use "Entire account", then narrow after the project exists)
4. Copy the token — it starts with `pypi-`

### 2. Store the token

```bash
# Option A: ~/.pypirc (persistent, machine-local)
cat >> ~/.pypirc << 'EOF'
[pypi]
  username = __token__
  password = pypi-YOUR_TOKEN_HERE
EOF
chmod 600 ~/.pypirc

# Option B: environment variable (CI-friendly)
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-YOUR_TOKEN_HERE
```

---

## Release checklist

### Step 1 — Verify tests pass

```bash
cd projects/voice_runtime
pytest tests/ -q --no-cov
# Expected: all pass or skip (azure skips without [azure] extra)
```

### Step 2 — Bump the version

Edit `pyproject.toml`:

```toml
[project]
version = "0.1.1"   # ← bump here
```

Follow semver:
- **Patch** (0.1.x) — bug fixes, no API changes
- **Minor** (0.x.0) — new backwards-compatible API
- **Major** (x.0.0) — breaking changes (see PORTING.md)

### Step 3 — Update CHANGELOG.md

Add an entry at the top:

```markdown
## 0.1.1 — 2026-05-10

### Fixed
- ...
```

### Step 4 — Clean previous builds

```bash
rm -rf dist/ build/ voice_runtime.egg-info/
```

### Step 5 — Build the distribution

```bash
python -m build
```

This produces two files in `dist/`:
- `voice_runtime-0.1.1.tar.gz` — source distribution
- `voice_runtime-0.1.1-py3-none-any.whl` — wheel

### Step 6 — Inspect the build

```bash
# List files in the wheel — confirm no internal credentials or NC-* docs ship
python -m zipfile -l dist/voice_runtime-0.1.1-py3-none-any.whl

# Check metadata
twine check dist/*
```

**What must NOT be in the wheel:**
- `feature-requests/` directory (NC-* internal docs)
- `.env`, `.env.example` (env template stays local)
- `tests/` directory

Add a `MANIFEST.in` if any of those appear (see note below).

### Step 7 — Test publish to TestPyPI first

```bash
twine upload --repository testpypi dist/*
```

Install from TestPyPI to verify:

```bash
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    voice-runtime==0.1.1
python -c "from voice_runtime import VoiceSession; print('✓')"
```

### Step 8 — Publish to PyPI

```bash
twine upload dist/*
```

### Step 9 — Tag the release

```bash
git tag v0.1.1
git push origin v0.1.1
```

---

## Controlling what ships in the wheel

By default setuptools includes everything under `voice_runtime/` (the package)
plus `README.md`, `LICENSE`, and `pyproject.toml`. It excludes `tests/` and
`feature-requests/` because they are not inside the package directory.

If inspection shows unwanted files, add `MANIFEST.in` to the project root:

```
# MANIFEST.in
exclude .env.example
recursive-exclude feature-requests *
recursive-exclude tests *
```

---

## Separate GitHub repository (recommended before PyPI)

The `voice_runtime` code currently lives inside the `yamlgraph` monorepo.
Publishing from a monorepo works, but a dedicated repo gives cleaner
issue tracking, CI, and release history for consumers.

Steps to extract (NC-230):

1. `git subtree split --prefix=projects/voice_runtime -b voice-runtime-standalone`
2. Create `github.com/your-org/voice-runtime`
3. `git push voice-runtime-standalone HEAD:main`
4. Set up GitHub Actions for CI and PyPI publish on tag push

This is a coordination decision between all three parties — do not extract
unilaterally.

---

## CI publish on tag (GitHub Actions skeleton)

```yaml
# .github/workflows/publish.yml
name: Publish to PyPI

on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build twine
      - run: python -m build
      - run: twine upload dist/*
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}
```

Store `PYPI_TOKEN` in the repository's GitHub Secrets.

---

## TestPyPI account

Register at https://test.pypi.org separately from pypi.org — separate accounts
and tokens. Useful for smoke-testing the release before going live.
