# NC-229: Standalone Voice Questionnaire Repository

**Status:** Proposed
**Date:** 2026-04-14
**Priority:** High
**Scope:** ninchat_voice + voice_runtime → `customer-service-agent-platform` repo

## Problem

The voice questionnaire system — **navigator** (intent routing), **medical triage** (callback screening), and **interRAI-CA** (elderly care assessment) — lives inside the yamlgraph framework monorepo:

| Project | Role | Lines (src) | Deploys |
|---|---|---|---|
| `projects/ninchat_voice/` | FSM coordinator, voice pipeline, graphs | ~5,900 | fly.io `ninchat-voice` |
| `projects/voice_runtime/` | TTS/STT providers, Twilio transport | ~1,700 | vendored into ninchat_voice |

### Why this is a problem

1. **yamlgraph is a framework, not an application** — ninchat_voice is an application built on yamlgraph, but lives inside the framework repo. Framework changes break the application; application FRs clutter framework history.

2. **Vendoring hack** — deploy.sh vendors `voice_runtime/` via `cp -R`. PYTHONPATH hacks substitute for proper packaging. This would break if either project moved independently.

3. **Independent release cadence** — voice features (NC-213, NC-216, NC-219) iterate on daily cycles. yamlgraph framework releases (v0.4.63) gate on unrelated CI (CAP tests, examples, demos). Unlocking one requires the other.

4. **Divergent handler duplication** — Handler functions (message management, gap detection, corrections, recap) are copy-pasted across graph modules with accidental drift (NC-228).

## Current Dependency Graph

```
┌──────────────────────────────┐
│          yamlgraph           │ ← Framework (pip install)
│  graph_loader, executor,     │
│  node_factory, llm_factory   │
└──────────┬───────────────────┘
           │ pip dependency
           ▼
┌──────────────────────────────┐    vendors    ┌─────────────────┐
│       ninchat_voice          │◄──────────────│  voice_runtime   │
│  FSM coordinator             │               │  TTS/STT/Twilio  │
│  graphs/ (navigator,         │               └─────────────────┘
│   medical_triage, interrai)  │
│  actions/ (real, stubs,      │
│   e2e_bridge, timed_mocks)   │
│  services/ (telephony, stt,  │
│   tts, ninchat, metrics)     │
│  config/ (6 FSM configs)     │
│  prompts/                    │
│  80 test files               │
└──────────────────────────────┘
```

**Stays in yamlgraph (not moving):**
- `projects/outcaller/` — outbound call test harness, separate concern
- `questionnaire-api/` — HTTP API + HTMX UI, separate Fly app, separate deployment

## Proposed Solution: `customer-service-agent-platform` Standalone Repository

### Repository Scope

One standalone repo containing everything needed to deploy and test the voice questionnaire system:

```
customer-service-agent-platform/
├── pyproject.toml              # Single package: ninchat-voice
├── Dockerfile
├── fly.toml
├── supervisord.conf
├── deploy.sh
│
├── voice_runtime/              # TTS/STT providers + transport
│   ├── providers/
│   │   ├── elevenlabs_tts.py
│   │   ├── elevenlabs_stt.py
│   │   ├── azure_tts.py
│   │   └── azure_stt.py
│   ├── transports/
│   │   ├── twilio_ws.py
│   │   ├── twilio_call.py
│   │   └── twilio_sms.py
│   ├── session.py
│   ├── audio.py
│   └── tts.py / stt.py
│
├── actions/                    # FSM actions
│   ├── real/                   # Production action implementations
│   ├── stubs/                  # Unit test stubs
│   ├── timed_mocks/            # Timing-realistic mocks
│   └── e2e_bridge/             # E2E test bridge
│
├── services/                   # Support services
│   ├── telephony.py
│   ├── stt.py / tts.py
│   ├── ninchat_session.py
│   ├── ninchat_result_service.py
│   ├── call_transcript.py
│   ├── metrics.py
│   ├── result_delivery.py
│   └── ...
│
├── config/                     # FSM YAML configs
│   ├── voice_coordinator_navigator.yaml
│   ├── voice_coordinator_triage.yaml
│   └── voice_coordinator_questionnaire.yaml
│
├── graphs/                     # All YAMLGraph graphs
│   ├── _common/
│   │   └── handlers.py        # NC-228: shared handler primitives
│   ├── medical_triage/
│   │   ├── graph.yaml
│   │   ├── schema.yaml
│   │   ├── prompts/
│   │   └── medical_triage.py
│   ├── interrai_ca/
│   │   ├── graph.yaml
│   │   ├── schema.yaml
│   │   ├── prompts/
│   │   ├── interrai_ca.py
│   │   └── scoring/
│   ├── navigator/
│   │   ├── graph.yaml
│   │   └── prompts/
│   ├── probe_recap/
│   │   ├── graph.yaml
│   │   └── prompts/
│   ├── intent-classifier.yaml
│   └── rewrite-response.yaml
│
├── prompts/                    # Top-level prompts
│   ├── classify_intent.yaml
│   └── ninchat_mediator.yaml
│
├── server_fsm.py               # Webhook server
│
├── tests/
│   ├── unit/                   # ~70 test files
│   ├── integration/            # LLM graph tests
│   └── e2e/                    # Full call flow tests
│
├── scripts/
├── docs/
└── feature-requests/
```

### What stays in yamlgraph

- `yamlgraph/` package (graph_loader, executor, node_factory, llm_factory, etc.)
- `examples/` (demo graphs)
- `reference/` (docs)
- `tests/` (framework tests)
- `projects/outcaller/` (outbound call harness)
- `questionnaire-api/` (HTTP API, separate Fly app)
- `fsm/` (statemachine-engine, separate PyPI package)
- Published as `pip install yamlgraph`

### What moves

| Component | From | To |
|---|---|---|
| Voice FSM coordinator | `projects/ninchat_voice/actions/` | `actions/` |
| Support services | `projects/ninchat_voice/services/` | `services/` |
| FSM configs | `projects/ninchat_voice/config/` | `config/` |
| Questionnaire graphs | `projects/ninchat_voice/graphs/` | `graphs/` |
| Top-level prompts | `projects/ninchat_voice/prompts/` | `prompts/` |
| Voice runtime | `projects/voice_runtime/` | `voice_runtime/` |
| Webhook server | `projects/ninchat_voice/server_fsm.py` | `server_fsm.py` |
| Deployment files | `projects/ninchat_voice/{Dockerfile,fly.toml,...}` | root |
| Tests | `projects/ninchat_voice/tests/` | `tests/` |

### Dependencies

```toml
[project]
name = "customer-service-agent-platform"
dependencies = [
    "yamlgraph>=0.4.63",           # Framework (pip)
    "statemachine-engine>=1.0.87", # FSM engine (pip)
    "fastapi>=0.104.0",
    "uvicorn>=0.24.0",
    "elevenlabs",
    "azure-cognitiveservices-speech>=1.48",
    "twilio>=9.0",
    "websockets>=12.0",
    "pydantic>=2.0",
    "PyYAML>=6.0",
    "prometheus-client>=0.20",
    "boto3>=1.34",
]
```

## Migration Plan

### Phase 0: Prerequisites (do first, within yamlgraph)

1. **NC-228: Handler deduplication** — Extract shared handlers to `graphs/_common/handlers.py`. Reduces duplication before move.
2. **Eliminate cross-project imports** — Any `from projects.outcaller.` imports in ninchat_voice must be replaced with local equivalents or removed before the split.

### Phase 1: Create Repository (~1h)

1. Create `customer-service-agent-platform` repo on GitHub
2. Copy ninchat_voice + voice_runtime files into new structure
3. Set up `pyproject.toml` with dependencies
4. Set up pre-commit hooks (ruff, radon, file-size, conventional commits)
5. Verify `pip install -e ".[dev]"` works

### Phase 2: Fix Imports (~1h)

1. Replace all `from projects.ninchat_voice.` → relative or package imports
2. Replace `from voice_runtime.` → stays (now a local package, not vendored)
3. Update graph.yaml `module:` paths if directory structure changed
4. Verify `python -c "import actions; import voice_runtime; import services"` works

### Phase 3: CI + Deployment (~1h)

1. Set up CI workflow (pytest, ruff, coverage gate)
2. Move Fly.io config (fly.toml, Dockerfile, supervisord.conf)
3. Update `deploy.sh` — no more vendoring voice_runtime (it's local now)
4. Set secrets in new repo (Fly.io, Twilio, ElevenLabs, Anthropic, Google, Azure, Tigris)
5. Smoke test: deploy and make one voice call

### Phase 4: Test Migration (~1h)

1. Move 80 test files, fix imports
2. Verify `pytest tests/unit/ -q --no-cov` passes
3. Verify integration tests pass with API keys
4. Run triage E2E: `./test-triage-e2e.sh`

### Phase 5: Cleanup Yamlgraph (~15m)

1. Remove `projects/ninchat_voice/` and `projects/voice_runtime/` from yamlgraph
2. Update yamlgraph README
3. outcaller and questionnaire-api remain as-is

## Impact on Remaining Projects

### outcaller

Currently imports `from projects.outcaller.nodes.stt import listen_and_transcribe` — this import lives in outcaller, not ninchat_voice. outcaller references voice_runtime for TTS/STT but does not import ninchat_voice code. **No breakage** from extracting ninchat_voice.

Outcaller E2E tests that call ninchat_voice (test-triage-answerer.sh) call the deployed Fly endpoint over the phone network — they don't import ninchat_voice Python. **No breakage.**

### questionnaire-api

questionnaire-api has its own handlers, scoring, and questionnaire definitions. It never imports from ninchat_voice. **No breakage.**

Future option: share questionnaire definitions via a common pip package. Out of scope for NC-229.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Broken imports after move | High | Phase 2 is dedicated to this. CI catches immediately. |
| Lost git history | Medium | Fresh start with commit crediting yamlgraph source hash. NC-XXX FR trail provides semantic history. |
| Deploy regression | High | Smoke test before cutting over Twilio webhook URL. Run old+new in parallel during transition. |
| Feature request numbering | Low | Continue NC-XXX series in new repo. |
| statemachine-engine coupling | Low | Stays as pip dependency. FSM engine is stable (v1.0.87). |

## Acceptance Criteria

1. `customer-service-agent-platform` repo exists with all graphs, actions, services, voice_runtime, tests
2. `pip install -e ".[dev]"` succeeds
3. `pytest tests/unit/ -q --no-cov` — all tests pass
4. `fly deploy` succeeds; voice call completes navigator→triage flow
5. `projects/ninchat_voice/` and `projects/voice_runtime/` removed from yamlgraph
6. No vendoring hacks — voice_runtime is a proper local package
7. outcaller and questionnaire-api unaffected in yamlgraph

## Out of Scope

- outcaller migration (stays in yamlgraph)
- questionnaire-api migration (stays in yamlgraph, separate Fly app)
- SIP transport (future FR)
- New questionnaire types (add in new repo after migration)
- Breaking yamlgraph API changes
