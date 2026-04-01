# ConveyorAI v2 -- Interface Contracts

Extracted from legacy Modulars/ codebase. Frozen 2026-04-01.
This file is the ONLY contract between legacy and v2. No legacy code is copied.

---

## 1. SwarmController Public Interface

The legacy `SwarmController` class (in `swarm_core/controller.py`) exposes exactly 10 public methods. These form the complete API surface.

```python
class SwarmController:
    def __init__(self, *,
        test_agent: SimpleAgent,
        coder_agent: SimpleAgent,
        judge_agent: SimpleAgent,
        chat_agent: SimpleAgent,
        context_guard_agent: SimpleAgent,
        pattern_agent: SimpleAgent,
        compression_agent: SimpleAgent,
        novelty_agent: SimpleAgent,
        stability_guard_agent: SimpleAgent,
        seed_prep_agent: SimpleAgent,
        directive_prep_agent: SimpleAgent,
        stability_prep_agent: SimpleAgent,
        root_dir: str,
    )

    def status(self) -> dict[str, Any]
    # Returns ~100 keys (see Section 3). Must not raise. Always returns dict.

    def pause(self) -> None
    # Pauses swarm execution. Idempotent.

    def resume(self) -> None
    # Resumes from paused state. Idempotent.

    def stop(self) -> None
    # Requests full stop. May be graceful or immediate.

    def queue_background_run(self, goal: TaskGoal, config: RunConfig, source: str) -> str
    # Queues a background swarm run. Returns queue_id (string).
    # Blocks until queued, not until completed.
    # source is a label for logging (e.g., 'ui', 'preflight').

    def launch_prepared_run(self) -> str
    # Launches a run that was prepared via preflight.
    # Returns run_id (string).
    # Raises if no prepared run is available.

    def run_rehearsal(self, profile: str, apply_if_better: bool) -> dict
    # Runs rehearsal simulation with given profile.
    # Returns dict with keys: rehearsal_id, profile, accepted, live_score,
    #   rehearsal_score, stage_manifest, report_path, trace_path, manifest_path.
    # apply_if_better: if True, hot-swaps better stage manifest.

    def review_preflight(self, target: str, decision: str, note: str) -> None
    # Reviews a preflight proposal with decision ('approve'/'reject').
    # target identifies which preflight to review.
    # Raises if target is not found.

    def respond_to_chat(self, text: str, config: RunConfig, mode: str,
                         conversation_context: str) -> dict
    # Handles local chat responses (not swarm execution).
    # mode: 'chat', 'health', 'architect', 'recap'.
    # Returns dict with keys: reply (str), background_instruction (str), swarm_health (str).
    # This is synchronous (called via asyncio.to_thread in legacy).
```

---

## 2. Data Types

### RunConfig

From `swarm_core/types.py`. Dataclass with defaults.

```python
@dataclass
class RunConfig:
    test_command: str = "python -m pytest {tests_path} -q"
    chat_history_limit: int = 8
    memory_distillation_enabled: bool = True
    compaction_interval_waves: int = 3
    memory_rule_limit: int = 6
    memory_breadcrumb_limit: int = 5
    adaptive_compaction_enabled: bool = True
```

Additional config values are read from `cl.user_session.get("run_config_overrides")` and merged. Overrides can include any of the above fields by name.

### TaskGoal

From `swarm_core/types.py`. Dataclass.

```python
@dataclass
class TaskGoal:
    prompt: str
    target_files: list[str]   # Files to operate on
    language: str             # "general", "python", "javascript", etc.
```

### BotProfile

From `bot_profiles_v3.py` via `build_swarm_profiles()`. The function returns a dict mapping role name to profile config.

```python
def build_swarm_profiles() -> dict[str, BotProfile]
# Returns dict with 13 keys (see Section 4).
# Each value has: name, model, fallback_models, system_prompt
```

---

## 3. Status Dict Schema

The `status()` method returns a dict with ~100 keys. These are the EXACT keys observed in the legacy `_send_status()` function and the `status()` aggregation inside the controller.

### Chat State
| Key | Type | Source |
|---|---|---|
| chat_mode | str | 'chat', 'health', 'architect', 'recap' |
| chat_turn_count | int | Rolling conversation turn count |
| queued_architect_instruction_count | int | Pending architect briefs |
| latest_architect_instruction | str | Most recent brief text |
| background_run_queue_depth | int | Pending background runs |
| background_run_active_goal | str | Current background goal |
| background_run_last_run_id | str | Last completed run ID |
| background_run_last_status | str | Last run status text |
| filesystem_queue_depth | int | Pending filesystem ops |
| filesystem_active_target | str | Target path |
| filesystem_last_path | str | Last operated path |
| filesystem_last_status | str | Last filesystem op status |
| filesystem_last_result | str | Last filesystem op result |

### Swarm Health
| Key | Type | Source |
|---|---|---|
| state | str | 'idle', 'running', 'paused', 'stopped' |
| phase | str | 'preflight', 'execution', 'review' |
| wave_name | str | Current wave identifier |
| wave_index | int | Current wave number |
| active_topology | list[str] | Active agent names |
| spawn_count | int | Total spawns |
| open_handoff_count | int | Pending handoffs |
| failure_memory_hits | int | Failure memory lookups |
| hallucination_confidence | float | 0.0-1.0 |
| hallucination_alert_count | int | Total alerts |
| latest_hallucination_alert | str | Last alert text |
| team_ideas_count | int | Brainstormed ideas |
| latest_brainstorm_summary | str | Summary text |
| recommendation | str | System recommendation |
| handoff_mismatch_count | int | Handoff errors |
| latest_handoff_brief | str | Last handoff text |
| rosetta_warning_count | int | Rosetta warnings |
| latest_rosetta_warning | str | Last warning text |
| return_failure_streak | int | Consecutive failures |
| directives_active | bool | Directive enforcement flag |
| unfinished_feature_count | int | Incomplete features |
| current_focus | str | Current focus area |

### Memory
| Key | Type | Source |
|---|---|---|
| local_memory_packet_count | int | Stored packets |
| local_memory_reuse_count | int | Cache hits |
| local_memory_invalidations | int | Invalidated entries |
| local_memory_pressure | float | 0.0-1.0 |
| local_memory_compaction_triggered | bool | Last compaction state |
| latest_local_memory_pressure | float | Last measured pressure |
| latest_local_memory_compaction_reason | str | Last compaction reason |
| local_memory_note | str | Latest memory note |
| local_memory_agent | str | Agent that wrote memory |
| local_memory_task_family | str | Task family label |

### Memory (Generation)
| Key | Type | Source |
|---|---|---|
| generation_memory_records | int | Total records |
| generation_memory_restores | int | Restores performed |
| generation_memory_latest_generation_id | str | Latest generation ID |
| generation_memory_latest_aspiration | str | Latest aspiration |
| generation_memory_latest_note | str | Latest note |
| generation_memory_path | str | File path to memory file |

### Memory (Compaction)
| Key | Type | Source |
|---|---|---|
| memory_rule_limit | int | Config: max rules |
| memory_breadcrumb_limit | int | Config: max breadcrumbs |
| compaction_interval_waves | int | Config: interval |
| adaptive_compaction_enabled | bool | Config: adaptive flag |
| memory_distillation_enabled | bool | Config: distillation flag |

### Model Routing
| Key | Type | Source |
|---|---|---|
| local_model_host | str | Local model host/IP |
| local_model_routes | dict | role -> {primary, fallback} |
| latest_local_model_name | str | Last model used |
| latest_local_model_lane | str | Lane identifier |
| local_api_inflight | int | Inflight requests |
| local_api_throttle_hits | int | Throttled requests |
| local_api_user_waiting | int | User wait time |
| local_api_swarm_waiting | int | Swarm wait time |
| local_api_last_lane | str | Last used lane |

### State/Rehearsal
| Key | Type | Source |
|---|---|---|
| stage_manifest_current | str | Current stage name |
| stage_manifest_next | str | Next stage name |
| stage_manifest_score | float | Current score 0-1 |
| stage_manifest_profile | str | Profile name |
| stage_manifest_preload_bundle | list[str] | Preloaded items |
| stage_manifest_required_tools | list[str] | Required tools |
| stage_manifest_report_checklist | list[str] | Report items |
| rehearsal_state | str | 'IDLE', 'RUNNING', 'COMPLETE' |
| rehearsal_profile | str | Active profile |
| rehearsal_report_path | str | Last report path |
| rehearsal_manifest_path | str | Last manifest path |
| rehearsal_trace_path | str | Last trace path |

### Preflight
| Key | Type | Source |
|---|---|---|
| prep_bundle_id | str | Preflight bundle ID |
| prep_goal | str | Preflight goal text |
| prep_status | str | 'NONE', 'PENDING', 'READY', 'LAUNCHED' |
| prep_ready_to_launch | bool | Launch readiness |
| prep_requested_tools | list[str] | Required tools |
| prep_required_testing_tools | list[str] | Testing tools |
| prep_required_reporting_tools | list[str] | Reporting tools |
| prep_required_diagnostics_tools | list[str] | Diagnostics tools |
| prep_requested_updates | list[str] | Required updates |
| prep_proposals | list[dict] | Preflight proposal items |

### Guards
| Key | Type | Source |
|---|---|---|
| guard_mode | str | 'NORMAL', 'ELEVATED', 'STRICT' |
| guard_interventions | int | Total interventions |
| latest_guard_action | str | Last action |
| latest_guard_reason | str | Last reason |
| ramp_level | int | Current ramp 0-N |

### Skills
| Key | Type | Source |
|---|---|---|
| active_skill_count | int | Active skills |
| skill_retool_count | int | Retool events |
| latest_skill_event | str | Last event text |

### Tests
| Key | Type | Source |
|---|---|---|
| test_command | str | Current test command |
| artifacts_path | str | Artifacts directory |
| standard_test_fallback_count | int | Test fallback count |
| latest_standard_test_reason | str | Last fallback reason |
| latest_standard_test_pack | str | Last test pack |

### Display
| Key | Type | Source |
|---|---|---|
| ui_suggestions | list[str] | UI prompt suggestions |
| ui_warnings | list[str] | UI warnings |

### Specialist Profiles
| Key | Type | Source |
|---|---|---|
| specialist_profiles | list[dict] | Runtime profile snapshots |

Each item in specialist_profiles has:
- agent_name, task_family, current_expert_trend, reuse_count, refresh_count, invalidations, success_rate

---

## 4. Agent Profiles

From `bot_profiles_v3.py` via `build_swarm_profiles()`. All profiles have this structure:
- name: str
- model: str (primary model identifier)
- fallback_models: list[str]
- system_prompt: str
- fallback_client_models: list[str] (optional, e.g., "openrouter/free")

Verified roles (13 total): test, coder, chat, judge, context_guard, pattern_finder, compression, novelty, stability_guard, seed_prep, directive_prep, stability_prep

Note: The exact model names and system prompts for each profile must be re-read from bot_profiles_v3.py when the bridge is available. The profile names above are verified from app.py's _build_controller() function.

---

## 5. Chainlit Command Routes

All commands are detected in `on_message` by examining `message.content.strip()`.

| Command | Handler Function | Behavior |
|---|---|---|
| `/status` | _send_status(controller) | Returns swarm status display |
| `/testcmd <command>` | Direct session set | Updates active test command |
| `/memory <profile>` | _set_memory_profile() | Sets memory profile (default/fast/deep/off) |
| `/adaptive <on\|off>` | _set_adaptive_compaction() | Toggles adaptive compaction |
| `/recap` | _handle_local_chat(controller, ..., "recap") | Summarizes chat history |
| `/health` | Sets mode to "health" | Returns health status in chat reply |
| `/architect` | Sets mode to "architect" | Returns architect suggestion |
| `/chat` | Sets mode to "chat" | Normal chat mode |
| Any other `/` command | Rejects with message | "Use the swarm command console" |
| Filesystem mention | _parse_filesystem_request() | Detects folder/file creation requests |

**Action Callbacks (button clicks):**
| Callback Name | Handler | Behavior |
|---|---|---|
| swarm_status | _send_status | Show status |
| chat_recap | _handle_local_chat | Recap |
| swarm_pause | controller.pause() | Pause swarm |
| swarm_resume | controller.resume() | Resume swarm |
| swarm_stop | controller.stop() | Stop swarm |
| runner_pytest | _set_runner | Set pytest test command |
| runner_dotnet | _set_runner | Set dotnet test command |
| runner_npm | _set_runner | Set npm test command |
| memory_default/fast/deep/off | _set_memory_profile | Set memory profile |
| adaptive_on/off | _set_adaptive_compaction | Toggle adaptive |
| prep_status | _send_status | Show preflight status |
| prep_launch | _launch_prepared_run | Launch prepared run |
| rehearsal_run | _run_rehearsal | Run rehearsal (mixed profile) |

**Inspection Actions:**
| Callback Name | Payload |
|---|---|
| swarm_status | {} |
| chat_recap | {} |

---

## 6. Model Routing (Verified)

Three model backends:

1. **Groq**: `OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)`
   - Used by: test_agent
   - Primary for verification/testing

2. **OpenRouter**: `OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)`
   - Fallback for: all agents except test_agent
   - Supports "openrouter/free" as a fallback model filter

3. **Local Ollama**: `OpenAI(base_url=desktop_ollama_base_url(), api_key="ollama")`
   - Primary for: coder, chat, judge, context_guard, pattern, compression, novelty, stability_guard, seed_prep, directive_prep, stability_prep
   - Availability check: socket connection to (host, port) with 0.5s timeout
   - Defaults: desktop_ollama_target() returns (host, port) tuple

---

## 7. Session State

Legacy stores runtime state in `cl.user_session` (Chainlit's per-session dict):

| Key | Type | Purpose |
|---|---|---|
| swarm_controller | SwarmController | Primary controller instance |
| active_test_command | str | Current test runner command |
| run_config_overrides | dict | User overrides for RunConfig fields |
| chat_transcript | RollingConversation | Rolling chat history buffer |

This means the legacy system is tightly coupled to Chainlit's session model. The v2 replacement must abstract this behind a `SessionState` interface.

---

## 8. Filesystem Request Parsing

The `_parse_filesystem_request()` function in app.py extracts file/folder creation requests from natural language. It looks for:
- Keywords: "folder", "directory", "file"
- Pattern: "called X" or "named X" to extract names
- Scope: "repo_root" or "project_root" (above modulars vs within project)
- Language detection: javascript -> .js, typescript -> .ts, else .txt
- Content: "Hello, world!" or "Ready to help." depending on request

This is a simple heuristic parser, not a general filesystem API.

---

## 9. Constraints and Guarantees

- SwarmController.status() must NEVER raise. It catches all exceptions and returns a dict.
- respond_to_chat is called synchronously via asyncio.to_thread. It must be thread-safe.
- Background runs are fire-and-forget. The queue must not block the UI.
- Rehearsal runs are synchronous in legacy (blocks Chainlit callback).
- Preflight review is synchronous.
- The controller is instantiated once per Chainlit session (on_chat_start).
- Each Chainlit session gets its own controller instance (no shared state across sessions).

---

## 10. Aspirational Capabilities (Not Verified in Live Code)

The following capabilities are referenced in status() keys but their internal implementation is opaque (inside the 3,978-line controller):

- **Skill evolution**: active_skill_count, skill_retool_count, latest_skill_event
- **Hallucination detection**: hallucination_confidence, hallucination_alert_count
- **Team collaboration**: team_ideas_count, latest_brainstorm_summary
- **Stage manifest hot-swapping**: stage_manifest_score comparison and swap
- **Rosetta warnings**: rosetta_warning_count, latest_rosetta_warning
- **Generation memory**: aspiration tracking across generations

These should be implemented as stubs initially, with real logic added after the parallel run phase when actual behavior can be observed.
