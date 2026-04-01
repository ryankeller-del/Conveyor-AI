# ConveyorAI Refactoring Plan

## Executive Summary
Massive codebase reorganization, cleanup, and standardization for maintainability.

---

## Phase 1: Project Structure Standardization

### 1.1 Create Proper Project Layout

**Recommended Structure:**
```
ConveyorAI/
├── src/
│   └── conveyorai/
│       ├── __init__.py
│       ├── __main__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── controller.py
│       │   ├── local_runtime.py
│       │   ├── types.py
│       │   ├── preflight.py
│       │   ├── rehearsal.py
│       │   ├── bots.py
│       │   ├── hallucination_guard.py
│       │   ├── failure_memory.py
│       │   ├── artifacts.py
│       │   ├── compaction.py
│       │   ├── spawn.py
│       │   ├── skill_evolution.py
│       │   ├── team_collab.py
│       │   ├── efficiency.py
│       │   ├── standard_tests.py
│       │   ├── reporting.py
│       │   ├── prompt_guard.py
│       │   ├── stability_guard.py
│       │   ├── chat_lane.py
│       │   ├── local_models.py
│       │   ├── rosetta.py
│       │   └── exceptions.py
│       ├── interfaces/
│       │   ├── __init__.py
│       │   ├── chat_interface.py
│       │   ├── api_interface.py
│       │   └── cli_interface.py
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── helpers.py
│       │   ├── logger.py
│       │   └── config.py
│       └── models/
│           ├── __init__.py
│           ├── session_state.py
│           ├── bot_profile.py
│           └── task_definitions.py
├── config/
│   └── default_config.yaml
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
│   ├── api.md
│   ├── architecture.md
│   └── development.md
├── scripts/
│   ├── start.py
│   ├── run_tests.py
│   └── lint.sh
├── pyproject.toml
├── requirements.txt
├── setup.py
├── README.md
└── .gitignore
```

### 1.2 Create Dependency Management Files

- **pyproject.toml** (or requirements.txt + setup.py)
- Pin all external dependencies (no loose `pip install`)
- Separate dev dependencies

---

## Phase 2: Code Cleanup

### 2.1 Remove Versioned Files

Delete or move archived:
- `app.py`, `app_v2.py`, `app_v3.py`
- `bot_profiles_v3.py`
- All `.bak` files
- Old run logs (`rehearsal.launch.log`, `flask.launch.log`)

### 2.2 Unify Imports

**Current Issues:**
- Relative vs absolute imports scattered
- No consistent import style (PEP 8 violations)
- Unused imports

**Fixes:**
- Standardize on absolute imports: `from conveyorai.core import controller`
- Remove unused imports systematically
- Add import sorting: `pip install isort`

### 2.3 Consolidate App Entry Points

**Current:**
- Multiple standalone scripts
- `modular_belt.py`
- `run_rehearsal.py`
- `generate_daily_report.py`

**Fixes:**
- Single CLI entry point via `src/conveyorai/__main__.py`
- Use click/argparse for consistent CLI interface
- Keep scripts as thin wrappers or remove

---

## Phase 3: Modular Organization

### 3.1 Extract Core Services

**swarm_core/controller.py** (3978 lines)
**SwarmController** class needs breakdown:**
- Extract logging
- Extract state management
- Extract orchestration logic
- Keep only controller logic in main class

Split proposal:
```
src/conveyorai/core/controller.py
├── src/conveyorai/core/orchestrator.py
├── src/conveyorai/core/logger.py
└── src/conveyorai/core/state_manager.py
```

### 3.2 Organize Test Directory

```
tests/
├── unit/
│   ├── test_controller.py
│   ├── test_local_runtime.py
│   └── ...
├── integration/
│   ├── test_chat_interface.py
│   └── test_integration_pipeline.py
├── fixtures/
│   └── sample_sessions/
└── conftest.py
```

---

## Phase 4: Configuration and Environment

### 4.1 Centralize Settings

- **config/default_config.yaml** - Default configurations
- **config/development.yaml** - Dev overrides
- **config/production.yaml** - Production overrides
- Environment variable support for secrets

### 4.2 Environment Management

- `.env` file with API keys, model paths
- Config loader that respects precedence: env vars > config files

---

## Phase 5: Documentation

### 5.1 Minimum Documentation Requirements

- **README.md** - Quick start, installation, usage
- **docs/api.md** - API references for all public methods
- **docs/architecture.md** - High-level design, architecture decisions
- **docs/development.md** - How to run tests, contribute

### 5.2 Inline Documentation

- Add docstrings to all public classes/methods (PEP 257)
- Type hints for all function signatures
- Module-level docstrings

---

## Phase 6: Testing Improvements

### 6.1 Current State Analysis

- Test files scattered
- Many self-check tests
- No clear test coverage

### 6.2 Test Improvements

**Add to pyproject.toml:**
```toml
[tool.pytest.ini_options]
minversion = "6.0"
addopts = "-ra -q --strict-markers"
```

**Add test coverage:**
```bash
pip install pytest-cov
```

---

## Phase 7: CI/CD Setup

### 7.1 GitHub Actions Workflow

- Run tests on push
- Linting checks (black, isort, flake8)
- Type checking (mypy)

---

## Implementation Order

**Week 1: Structure & Dependencies**
1. Set up directory structure
2. Create pyproject.toml & requirements.txt
3. Move swarm_core files to src/conveyorai/core

**Week 2: Code Cleanup**
1. Delete versioned files
2. Unify imports
3. Extract reusable utilities

**Week 3: Refactoring Core**
1. Break down controller.py
2. Reorganize test infrastructure
3. Extract configuration system

**Week 4: Documentation & Polish**
1. Write documentation
2. Add type hints
3. Update entry points
4. Final cleanup

---

## Success Criteria

- [ ] 0 versioned files (`.bak`, `*_v2.py`, etc.)
- [ ] All imports use absolute paths
- [ ] All modules have docstrings
- [ ] 80%+ test coverage
- [ ] Documentation covers core functionality
- [ ] Single CLI entry point
- [ ] Dependencies properly pinned
- [ ] CI/CD pipeline working

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking backward compatibility | Maintain legacy wrapper functions |
| Large refactoring timeline | Prioritize Phase 1, 2, then iterate |
| Test failures during migration | Verify current tests before changes |
| Dependency conflicts | Start with clean environment, document versions |

---

## Next Steps

1. Review and approve this plan
2. Create backup branch `maintainance/legacy-stable`
3. Begin Phase 1
4. Document decisions in `docs/REFACTORING_LOG.md`