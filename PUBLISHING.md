# Publishing voice-runtime to PyPI

Publishing happens only through GitHub Actions (`.github/workflows/publish.yml`)
using the PyPI OIDC trusted publisher — no API tokens, no manual `twine upload`.
The step-by-step flow is in [reference/release-checklist.md](reference/release-checklist.md).

---

## Inspecting a build locally (optional)

```bash
rm -rf dist/ build/ voice_runtime.egg-info/
python -m build
# Confirm no internal credentials or NC-* docs ship
python -m zipfile -l dist/voice_runtime-*.whl
twine check dist/*
```

**What must NOT be in the wheel:**
- `feature-requests/` directory (NC-* internal docs)
- `.env`, `.env.example` (env template stays local)
- `tests/` directory

Add a `MANIFEST.in` if any of those appear (see note below).

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
