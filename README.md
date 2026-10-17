# ACW — Adaptive Context Workspace

A **local-only, command-first execution workspace** for launching approved Python scripts and observing asynchronous jobs. The frontend uses a single HTML5/CSS/JavaScript file with translucent panels, configurable cyberpunk themes, and multiple execution layouts. The backend runs Python scripts through a **pywebview JavaScript ↔ Python bridge**. **No FastAPI server, network listener, CDN, or cloud service is required.**

> **Status:** Early working prototype / v4 foundation. Designed for local desktop use. Not yet a hardened production job scheduler.

## Screenshots

UI screenshots will be added here later. Put images in `screenshots/` and reference them from this section.

## Features

- Persistent toolbar with monitor checkboxes, layout selector, live date/time, and user preferences
- Expandable terminal (`Enter` executes; `Shift+Enter` inserts a newline)
- Configurable recent action history (1–20 lines)
- Real asynchronous Python subprocess execution, with a controlled script catalog
- Task → Working Parts → Output panels for each execution
- Connected, flow, layered 2.5D, and collapsed layout modes
- Four themes (Cyan Cyberpunk default, Quantum Violet, Matrix Green, Industrial Amber)
- Glass opacity, edge glow, compact-density preferences (saved locally in the embedded webview)
- Job status, elapsed time, CPU and task memory via `psutil`, stdout, stage events, progress, and optional quality
- Job cancellation, limited concurrency, and JSON execution snapshots in `runs/`

## Requirements

- Python 3.10+
- Desktop OS with a supported pywebview renderer (Windows, macOS, or Linux)
- OS-specific GUI/webview dependencies may be needed on Linux; consult [pywebview installation documentation](https://pywebview.flowrl.com/guide/installation.html)

## Run locally

```bash
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Install dependencies and launch:

```bash
python -m pip install -r requirements.txt
python main.py
```

This opens a desktop window. **Opening `ui/index.html` directly in Chrome or Edge will not connect to Python**, because the pywebview bridge is only injected into its own window.

## Console commands

```text
help
catalog
run <command>
list
layout connected
layout flow
layout layered
layout collapsed
stop <job-id>
clear
```

Scripts in the catalog are discovered when the app starts. The UI loads their commands and descriptions from the script metadata and offers a selector to launch them. For example, `run organize` starts the Downloads organizer. Its default target is `~/Downloads`; to run it directly, use `python scripts/organize_downloads.py`, pass `--target <folder>` to select another folder, or use `--dry-run` to preview moves.

`run demo` and `run quality` start illustrative local example workflows. Job runtime, process CPU, and process memory readings are real measurements when `psutil` is available. CPU sampling is approximate. "System memory" is not currently collected and is labeled accordingly in the UI.

## Project structure

```text
acw-local/
├── main.py                  # Desktop entrypoint
├── requirements.txt
├── README.md
├── .gitignore
├── acw/
│   ├── __init__.py
│   ├── api.py                # Exposed bridge methods
├── methods/
│   └── engine.py             # Job registry, subprocesses, telemetry
├── ui/
│   └── index.html            # All-in-one HTML/CSS/JS frontend
├── scripts/
│   ├── demo_pipeline.py
│   ├── quality_check.py
│   └── organize_downloads.py
└── screenshots/              # Add UI captures here
```

## Adding scripts

Place a trusted `.py` script in `scripts/` and add these comments near the top (within the first 20 lines):

```python
# ACW_COMMAND: my-command
# ACW_DESCRIPTION: Short explanation shown in the catalog
# ACW_LABEL: Optional friendly name shown in the UI
```

`ACW_COMMAND` and `ACW_DESCRIPTION` are required; `ACW_LABEL` is optional and defaults to a title-cased command. Commands use lowercase letters, numbers, and hyphens, and must be unique. Restart the app to discover newly added scripts, then run one using `run my-command` or select it in the UI. Scripts without ACW metadata are not exposed. The catalog is read once when the app starts; the scripts folder is not monitored.

The process runner uses `shell=False` and only launches scripts discovered in the `scripts/` folder. Only add scripts you trust: they execute with your user account's privileges.

### Optional structured progress events

Scripts can print a JSON event on its own line to report stage/progress/quality:

```python
import json
print('ACW_EVENT:' + json.dumps({
    'stage': 'Validation', 'progress': 70, 'quality': 98
}), flush=True)
```

Any other stdout line is treated as ordinary process output. The engine retains up to 250 output lines and 250 events per job in memory. A maximum of 100 recent job records is kept in memory. Completed jobs are written to `runs/<job-id>.json` (ignored by Git).

Scripts may emit progress updates while they run; when a script exits successfully, the engine marks its stage as `Complete` and progress as 100%.

## Architecture and limitations

- **No listening service:** Python and the HTML frontend communicate through pywebview's local JavaScript API bridge.
- **Concurrent execution:** Jobs run as subprocesses in background threads, so the UI remains responsive.
- **Local persistence:** Preferences use webview localStorage; completed run records are JSON files under `runs/`.
- **Security boundary:** This is a local desktop app, not a sandbox. Scripts can access resources available to the current OS user. Do not register untrusted scripts.
- **Cancellation:** Attempts to terminate the job and child processes. Some subprocess trees may require additional platform-specific cleanup.
- **Telemetry:** Best-effort CPU/memory sampling, not a full tracing or profiling system. Stage timing, per-stage resources, input versioning, file verification, historical comparison, and multi-step workflow DAGs are future work.
- **Rendering:** Layered mode uses CSS perspective, not a WebGL engine.
- **Platform testing:** Behavior can vary across desktop webview engines; validate on your target OS before relying on it for critical jobs.

## Roadmap

- [ ] Configurable script definitions and argument schemas
- [ ] Job filtering, search, saved workspace layouts, and pinned groups
- [ ] Stage-level timing and metrics, resource graphs, and comparison views
- [ ] Real artifact discovery and safe file-opening actions
- [ ] More robust process-tree cancellation, timeouts, and job persistence
- [ ] Workflow dependencies and agent/tool-call telemetry
- [ ] Automated tests and desktop packaging

## License

No license is included yet. Add one if you intend to grant reuse or redistribution rights.
