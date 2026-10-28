# Release Checklist — voice_runtime

Releases happen only through GitHub Actions: version bump via PR → merge →
push a `vX.Y.Z` tag → `publish.yml` publishes to PyPI. No manual `twine upload`.

**CI gate:** `publish.yml` runs the test job (Python 3.11 + 3.12, extras
`dev,elevenlabs,azure`) on every PR to `main` and on `v*.*.*` tags. On tags it
also validates tag == `pyproject.toml` version, builds, publishes via the PyPI
OIDC trusted publisher, and creates the GitHub Release.

---

## Steps

```bash
# 1. Release PR: bump version in pyproject.toml and date the CHANGELOG entry
#    (main is PR-only — never push to main directly)
git switch -c chore/release-0.1.1 origin/main
sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml
cat > ./tmp/msg.txt << 'EOF'
chore(release): bump version to 0.1.1

- NC-283: Twilio WebSocket signature validation (X-Twilio-Signature)
EOF
git commit -aF ./tmp/msg.txt
git push -u origin chore/release-0.1.1
gh pr create --base main --fill

# 2. After the PR is green and merged: tag the merge commit on main
git fetch origin
git tag v0.1.1 origin/main
git push origin v0.1.1

# 3. Watch the publish run
gh run watch "$(gh run list --workflow publish.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

The version bump may also ride in the feature PR itself; the tag still goes on
the merge commit on `main`.

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

The PR run of `publish.yml` must be green. Before tagging, also confirm locally:

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
| Tag pushed on wrong commit | Delete the tag before the publish job runs (`git push origin :refs/tags/vX.Y.Z`); after publish, bump to next patch |
| PyPI: "File already exists" | Version already published; cannot overwrite — bump to next patch |
| Tag pushed but no CI | Tag must match `v*.*.*` glob exactly (e.g., `v0.1.1` not `0.1.1`) |

---

## After release

- Update `projects/outcaller/requirements.txt` to `voice-runtime[elevenlabs]>=X.Y.Z`
- Install new version into any consuming venvs: `pip install --upgrade voice-runtime`
