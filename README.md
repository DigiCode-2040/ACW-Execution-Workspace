# ACW — Advanced Control Workspace

ACW is a local desktop workspace for running trusted Python scripts and Windows batch files. The frontend communicates with the Python engine through pywebview's JavaScript bridge. ACW has no HTTP listener or remote execution service.

The app is intended for personal, local automation. Approved scripts run with the current user's operating-system permissions; ACW is not a sandbox or hardened production scheduler.

## Features

- Catalog of Python scripts and explicitly registered Python or Windows batch files
- Script discovery and registration using descriptive `ACW_...` header metadata
- Direct Python subprocess execution, plus controlled `cmd.exe` launch for registered `.bat` and `.cmd` files
- FIFO queue with a configurable concurrency limit
- Job progress, structured stage events and timings, captured output, CPU and task-memory readings when `psutil` is available
- Collapsed dashboard and Canvas layouts, with filters, search, draggable job cards, pan, and zoom
- Stop controls, current-session Log Audit, and search of saved run records
- Safe Open/Reveal actions for artifacts reported from approved output locations
- Auto-restored workspace and named sessions
- Successful-run history, runtime averages, and comparison by script
- Local UI preferences, themes, and keyboard-accessible controls

## Requirements and launch

- Python 3.10+
- Windows, macOS, or Linux with a supported pywebview renderer
- Dependencies listed in `requirements.txt`

Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

On macOS/Linux, activate with `source .venv/bin/activate`. Then install dependencies and start ACW:

```text
python -m pip install -r requirements.txt
python main.py
```

Opening `ui/index.html` directly in a browser does not connect to the Python bridge.

## Using the workspace

Common terminal commands:

```text
help
catalog
catalog reload
run <command> [python-script-arguments...]
list
layout collapsed
layout canvas
stop <job-id>
clear
session save [name]
session load <name>
session list
session clear
history [script]
compare <script>
concurrent
concurrent <N>
```

Scripts in `scripts/` are discovered on startup; use `catalog reload` or the reload button to rescan. The script selector runs the selected command without arguments. Python scripts can also be started in the terminal with arguments, which ACW passes as a list without invoking a shell.

Manage Work registers a script in place: ACW saves its path and working directory but does not copy or edit it. Select the trust checkbox to confirm that the script may run with your account's permissions. Missing or changed registered batch headers make the entry unavailable until corrected and reloaded.

The concurrency limit defaults to 4. Jobs beyond the limit wait in a FIFO queue. Job cancellation requests process termination; process-tree cleanup is best effort and depends on the platform.

## Script headers

Metadata is read from the first 20 lines. `ACW_COMMAND` and `ACW_DESCRIPTION` are required for discovered Python scripts and all registered batch scripts. `ACW_LABEL` is optional and defaults to a title-cased command name. `ACW_OUTPUT_DIR` may be repeated to declare artifact output directories; relative paths are resolved from the project root.

### Python

```python
# ACW_COMMAND: organize
# ACW_DESCRIPTION: Sort files in a target directory
# ACW_LABEL: Organize Files
# ACW_OUTPUT_DIR: reports
```

Python scripts placed in `scripts/` with valid headers are discovered automatically. Existing Python scripts registered through Manage Work may omit these headers; ACW uses the values supplied in the registration form.

### Windows batch

Register `.bat` and `.cmd` scripts through Manage Work. A valid header is required and pre-fills the registration name, label, and description:

```bat
@echo off
REM ACW_COMMAND: cleanup-temp
REM ACW_DESCRIPTION: Clean application temporary files
REM ACW_LABEL: Clean Temp Files
REM ACW_OUTPUT_DIR: reports

echo Cleanup complete
```

Batch headers may also use `:: ACW_KEY: value` lines. `REM` is recommended. ACW checks the header again when loading or reloading the catalog and when starting a job. The registered command must continue to match `ACW_COMMAND`.

**Batch jobs do not accept runtime arguments in this version.** ACW starts them through `cmd.exe /d /s /c`, with the script path quoted and no user-supplied command text appended. Paths containing cmd.exe metacharacters are rejected. Batch scripts themselves can still run arbitrary commands, so only register files you have reviewed and trust.

## Progress events and artifacts

Python scripts can print a structured event on its own line to report stage, progress, or quality:

```python
import json

print("ACW_EVENT:" + json.dumps({
    "stage": "Validation",
    "progress": 70,
    "quality": 98,
}), flush=True)
```

To report an artifact for safe Open/Reveal actions:

```python
print("ACW_EVENT:" + json.dumps({
    "artifact": "reports/result.pdf",
}), flush=True)
```

Relative artifact paths are resolved from the project root. Open/Reveal is limited to reported paths under the project or a declared `ACW_OUTPUT_DIR`; Open is additionally limited to supported document, image, and archive types. Ordinary stdout and stderr are captured together. ACW retains a bounded number of output lines and events per job.

## Sessions, history, and audit

ACW stores local execution data under `runs/`:

- `runs/<job-id>.json`: job snapshot
- `runs/history.jsonl`: compact index of successful runs, used for history and runtime averages
- `runs/last_session.json`: automatically saved workspace
- `runs/registered_scripts.json`: registered script paths and metadata
- `sessions/`: named workspace snapshots

Jobs that were active at shutdown are restored as interrupted, not restarted. Log Audit shows jobs started in the current app session and can search available saved snapshots, sessions, and successful-run history. It is an inspection view, not a complete archival guarantee for every failed or interrupted run.

Session files are validated before loading; an invalid file leaves the current workspace intact. Malformed saved state is ignored during startup and omitted from the session list. Saved jobs retain their declared artifact output directories, including when a script is later removed from the catalog. Older snapshots recover these directories from the script's current catalog entry when available.

## Security and limitations

- Only catalog-approved scripts can be started from the workspace.
- Python scripts are launched as argument lists with `shell=False`.
- Batch execution necessarily uses Windows `cmd.exe`; it is restricted to registered, trusted scripts and currently receives no runtime arguments. This is not a security sandbox.
- Scripts can access anything available to the current OS user. Review scripts before registration and do not register untrusted code.
- Resource monitoring is best effort. CPU sampling is approximate; system memory is not collected.
- UI and process behavior can vary between platform webview implementations.

## Project structure

```text
ACW-Execution-Workspace/
├── acw/                 # Python API bridge and job engine
├── scripts/             # Discovered Python scripts
├── tests/               # Engine and registration tests
├── ui/                  # HTML/CSS and JavaScript frontend
├── main.py              # Desktop entry point
├── requirements.txt
└── README.md
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
