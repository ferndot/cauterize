# cauterize

**Self-healing Python.** When a runtime exception occurs in a decorated function, cauterize intercepts it, asks Claude to generate a fix, validates and replays it, then hot-patches the live process — no restart required.

## Install

```bash
pip install cauterize
# With framework extras:
pip install "cauterize[fastapi]"
pip install "cauterize[django]"
pip install "cauterize[celery]"
```

Requires `ANTHROPIC_API_KEY` in the environment (or pass `api_key=` to `configure()`).

---

## Quickstart

### Auto mode — instrument everything

Call `cauterize.install()` before your framework is imported. Every route handler and Celery task is wrapped automatically.

```python
# main.py
import cauterize

cauterize.install()          # must come before framework imports
cauterize.configure(
    slack=cauterize.SlackNotifier(webhook_url="https://hooks.slack.com/..."),
    jira=cauterize.JiraCard(
        base_url="https://myorg.atlassian.net",
        project_key="ENG",
        auth=("you@example.com", "jira_api_token"),
    ),
)

from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def index():
    return {"ok": True}
```

Opt individual functions out with `@cauterize.exclude`:

```python
@cauterize.exclude
@app.get("/webhook")
def stripe_webhook(request: Request):
    ...
```

### Manual mode — opt-in per function

```python
import cauterize

cauterize.install(mode="manual")

@cauterize.heal
def risky_calculation(x: int) -> int:
    return 100 // x          # ZeroDivisionError? cauterize will fix it.
```

---

## How it works

1. **Intercept** — `@cauterize.heal` wraps the function in a try/except retry loop (sync and async).
2. **Check eligibility** — builtins, C extensions, magic methods, and `cauterize.*` itself are never patched.
3. **Rate-limit** — each `(function, exc_type)` pair gets at most `max_retries` attempts per hour.
4. **Ask Claude** — the function source, traceback, and local variable types (not values) are sent to Claude with a structured tool-use prompt. Claude returns `fixed_source`, `explanation`, and `confidence`.
5. **Validate** — the patch is AST-checked: must compile, signature unchanged, no new imports, no dangerous builtins, line count ≤ 3× original.
6. **Replay** — the fixed function is executed with the original arguments. If it raises, the patch is discarded.
7. **Commit** — `setattr(module, func_name, new_func)` hot-patches the live module.
8. **Notify** — Slack message + Jira card are created in a background thread.

---

## Configuration

```python
cauterize.configure(
    api_key="sk-ant-...",           # default: ANTHROPIC_API_KEY env var
    model="claude-opus-4-6",        # Claude model for patch generation
    confidence_threshold=0.85,      # minimum confidence to apply a patch (0–1)
    max_retries=3,                  # max heal attempts per function/exc_type per hour
    dry_run=False,                  # if True: generate and log patches but don't apply
    audit_path="/var/log/cauterize.jsonl",   # JSON Lines audit log
    slack=cauterize.SlackNotifier(webhook_url="..."),
    jira=cauterize.JiraCard(
        base_url="https://myorg.atlassian.net",
        project_key="ENG",
        auth=("user@example.com", "api_token"),
    ),
)
```

---

## Framework support

| Framework | Integration | Notes |
|-----------|-------------|-------|
| FastAPI   | built-in    | Patches `APIRouter.add_api_route` at import time |
| Django    | built-in    | Patches `View.dispatch` (CBV) and `path()`/`re_path()` (FBV) |
| Celery    | built-in    | Patches `Task.__init_subclass__`; no pre-commit replay (uses Celery retry) |

### Third-party integrations

Register via entry points in `pyproject.toml`:

```toml
[project.entry-points."cauterize.integrations"]
myframework = "mypackage.cauterize_integration:MyFrameworkIntegration"
```

Implement the `Integration` protocol from `cauterize.integrations._base`.

---

## Modes

| Mode | Behaviour |
|------|-----------|
| `"auto"` | All framework routes and tasks are wrapped; `@cauterize.exclude` opts out |
| `"manual"` | Nothing is wrapped automatically; `@cauterize.heal` opts in |

---

## Audit log

Every heal attempt is appended to the configured `audit_path` as a JSON Lines record:

```json
{"timestamp":"2026-03-12T10:00:00Z","func":"process_order","exc_type":"KeyError","status":"healed","confidence":0.91,"attempt":1,"detail":"Added missing key guard"}
```

Statuses: `healed`, `rejected` (low confidence), `failed` (validation/replay error).

---

## Safety

- Dangerous builtins (`eval`, `exec`, `open`, `__import__`) and shell calls (`os.system`, `subprocess.*`) are rejected by the AST validator.
- Patches that change a function's signature or add new imports are rejected.
- Patches that fail the pre-commit replay with the original arguments are discarded.
- `cauterize.*` modules are in `_PROTECTED_MODULES` and will never self-patch.
- Healing is skipped for `KeyboardInterrupt`, `SystemExit`, I/O errors, and other non-recoverable exception types.
