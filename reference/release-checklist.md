# Release Checklist — voice_runtime

Bump → commit → tag → push tags → CI publishes to PyPI automatically.

**CI gate:** `publish.yml` runs on `v*.*.*` tags. It validates that the tag version
matches `pyproject.toml` before publishing. A mismatch fails the build.

---

## Steps

```bash
# 1. Pull latest (avoid divergence)
git pull

# 2. Bump version in pyproject.toml
# patch: X.Y.Z → X.Y.(Z+1)  minor: X.Y.Z → X.(Y+1).0  major: X.Y.Z → (X+1).0.0
sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml

# 3. Stage and commit
cat > /tmp/vr-release-msg.txt << 'EOF'
chore(release): bump version to 0.1.1

- NC-283: Twilio WebSocket signature validation (X-Twilio-Signature)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
EOF
git add pyproject.toml
git commit -F /tmp/vr-release-msg.txt

# 4. Tag (must match version in pyproject.toml exactly)
git tag v0.1.1

# 5. Push commit + tag (order matters — commit first, then tags)
git push
git push --tags
```

CI picks up the tag, runs tests on Python 3.11 + 3.12, builds, validates tag == pyproject.toml, then publishes to PyPI via OIDC trusted publisher.

---

## Post-publish: bump consumer pins

After CI confirms the publish succeeded (`gh run list --limit 1`), bump
the pinned version in every consumer repo. For **ninchat_voice**:

```bash
cd ../ninchat_voice
# Update exact pin (deploy) and minimum pin (dev)
sed -i '' 's/voice-runtime\[azure,elevenlabs\]==OLD/voice-runtime[azure,elevenlabs]==NEW/' requirements-deploy.txt
sed -i '' 's/voice-runtime\[azure,elevenlabs\]>=OLD/voice-runtime[azure,elevenlabs]>=NEW/' pyproject.toml
# Update version assertion in deploy test
# tests/test_nc288_supervisor_call_isolation.py
pytest tests/ -q --no-cov
git add -A && git commit -m "chore(deps): bump voice-runtime to NEW"
```

**Why this matters:** `pip install -e ../voice_runtime` locally shadows
the PyPI pin. Code that uses new API will pass locally but crash in
production if the deploy pin still references the old version. The
failure mode is silent — daemon threads die without structured logging.
See NC-315 for the incident record.

---

## Version scheme

`MAJOR.MINOR.PATCH` — semver:

| Change type | Bump |
|-------------|------|
| Security fix, bug fix | PATCH |
| New feature, new transport | MINOR |
| Breaking API change | MAJOR |

---

## Pre-release checks

Before tagging, confirm locally:

```bash
# All tests pass
pytest tests/ -q --no-cov

# No ruff violations
ruff check voice_runtime/

# Package builds cleanly
pip install build && python -m build --wheel --no-isolation
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| CI: "Tag version != pyproject.toml version" | Ensure `sed` replaced the right string; check `git diff pyproject.toml` |
| CI: tests fail on ubuntu (no `ffmpeg`) | CI installs ffmpeg via apt — check azure/elevenlabs optional dep imports |
| PyPI: "File already exists" | Version already published; cannot overwrite — bump to next patch |
| Tag pushed but no CI | Tag must match `v*.*.*` glob exactly (e.g., `v0.1.1` not `0.1.1`) |

---

## After release

- Update `projects/outcaller/requirements.txt` to `voice-runtime[elevenlabs]>=X.Y.Z`
- Install new version into any consuming venvs: `pip install --upgrade voice-runtime`
