# ACW — Advanced Control Workspace

ACW is a local desktop app for running trusted Python scripts and Windows batch files, combining them into reusable groups, and scheduling work while the app is open. Its workspace focuses on active jobs, recent results, and failures that need attention.

The HTML/CSS/JavaScript frontend communicates with the Python engine through pywebview's JavaScript bridge. ACW has no HTTP listener or remote execution service, and scheduling does not install operating-system tasks or services.

The app is intended for personal, local automation. Approved scripts run with the current user's operating-system permissions; ACW is not a sandbox or hardened production scheduler.

## Features

- Catalog of Python scripts and explicitly registered Python or Windows batch files
- Script discovery and registration using descriptive `ACW_...` header metadata
- Direct Python subprocess execution, plus controlled `cmd.exe` launch for registered `.bat` and `.cmd` files
- FIFO queue with a configurable concurrency limit
- Saved script groups with ordered sequential or parallel execution
- App-only schedules for a script or group: once, every hour, or every 24 hours
- Job progress, structured stage events and timings, captured output, CPU and task-memory readings when `psutil` is available
- Collapsed dashboard and Canvas layouts, with filters, search, draggable job cards, pan, and zoom
- A default 15-minute job view that keeps active jobs and undismissed failures visible
- A live feed of the latest 20 events, with Pause/Resume, Clear feed, and scroll preservation
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

For a first run, select **Demo Pipeline** in the workspace and click **Run selected**, or enter `run demo` in the terminal. It reports example stages and progress. **Quality Check** is another illustrative task. **Organize Downloads** moves files in `~/Downloads`; preview its actions first with `run organize --dry-run`, or supply a different folder with `run organize --target "path/to/folder" --dry-run`.

## Navigation

| Control | Purpose |
| --- | --- |
| Monitors | Choose the metrics shown in job details. |
| Layout | Switch between the Collapsed dashboard and draggable Canvas view. |
| Workspace | Return to execution and reset job filters to the default 15-minute view. |
| Manage Work | Register or relink scripts, create groups, and manage schedules. |
| Log Audit | Inspect current-session jobs or search saved run details. |
| Preferences | Change themes, display density, and other local UI settings. |

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

The existing time dropdown defaults to **Last 15 minutes**: queued, running, and cancelling jobs, plus jobs finished within the last 15 minutes. This window uses completion time, so a long-running job stays visible after it finishes. Failed jobs remain visible until you use **Dismiss**, including after reopening ACW. Dismissal hides a failure from the default view and retains its audit details. Choose **Any time** in the same dropdown to see the full current workspace, including dismissed failures. The last-hour and last-24-hours options filter by start time. Status and text filters can narrow each view further.

In the Collapsed layout, **Live events** shows the latest 20 events from active jobs and jobs finished within 15 minutes, independently of the jobs filters. An old failure stays in the job view until dismissed, but its old events do not fill the live feed. **Pause** freezes the feed and **Resume** catches up to the latest events. Updates preserve your reading position. **Clear feed** hides events already received without deleting job logs; new events appear afterward, or after resuming if paused. Older runs and their available saved details remain accessible in Log Audit.

## Groups and schedules

Groups and schedules are managed through **Manage Work**, rather than terminal commands. Definitions are saved locally and remain available after restarting ACW.

In **Manage Work → Script groups**, select at least two catalog scripts, arrange their order with the arrow buttons, and choose sequential or parallel execution. Save the group, then use **Run group** to start it. Groups can be edited or deleted; editing a definition does not change a run already in progress. Group members run without additional command-line arguments.

- **Sequential** starts the next script only after the previous script completes successfully. A failed or cancelled member stops the remaining steps.
- **Parallel** submits all members to the normal workspace queue and respects the global concurrency limit.
- **Stop run** cancels submitted members and prevents any remaining sequential steps from starting. The same group cannot start again while its previous members are still active.

Individual jobs appear in the workspace, include their group name, and remain available in Log Audit. Recent group-run summaries are shown for the current app launch. Saved group definitions persist across launches; unfinished groups are never resumed automatically.

In **Manage Work → Schedules**, choose a script or saved group, a first run date and time, and **Once**, **Every 1 hour**, or **Every 24 hours**. Dates are entered and displayed in your local timezone and stored in UTC. Repeating schedules use elapsed hours, so a 24-hour interval may shift its local clock time across daylight saving changes. Schedules can be paused, resumed, or deleted.

Scheduling runs inside ACW while the app is open; no operating-system task, service, or background launcher is installed. Closing ACW stops future scheduling and sequential group steps. An already launched script follows the existing process shutdown behavior; use Stop before closing if it must be cancelled. On reopening, missed one-time schedules are marked inactive, and repeating schedules advance to their next future occurrence. Missed occurrences are never replayed as a backlog. If a target is already active when its schedule is due, that occurrence is skipped. Failed launches and skipped occurrences are shown in the schedule's latest result. Deleting a group pauses its schedules.

Definitions are saved in `runs/automation.json`. Scheduling is intended for local convenience, not guaranteed delivery: the app must be open and the computer awake. A one-time schedule that has passed must be recreated with a future date to run again.

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

ACW stores execution data under `runs/` and named workspaces under `sessions/`. These files stay local and are excluded from version control:

- `runs/<job-id>.json`: job snapshot
- `runs/history.jsonl`: compact index of successful runs, used for history and runtime averages
- `runs/last_session.json`: automatically saved workspace
- `runs/registered_scripts.json`: registered script paths and metadata
- `runs/automation.json`: saved script groups and schedules
- `sessions/`: named workspace snapshots

Jobs that were active at shutdown are restored as interrupted, not restarted. Log Audit shows jobs started in the current app session and can search available saved snapshots, sessions, and successful-run history. It is an inspection view, not a complete archival guarantee for every failed or interrupted run.

**Any time** shows jobs retained in the current workspace, rather than every historical run on disk. Finished jobs may be pruned as new jobs arrive; undismissed failures and members needed by an active group are retained. Use Log Audit to search older saved records. Loading or clearing a workspace leaves saved group definitions and schedules in place.

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
├── acw/                 # Python bridge, execution engine, groups, and scheduler
├── scripts/             # Discovered Python scripts
├── tests/               # Python regression tests and headless browser checks
├── ui/                  # HTML/CSS and JavaScript frontend
├── main.py              # Desktop entry point
├── requirements.txt
└── README.md
```

## Checks

Run the Python regression suite from the project root:

```text
python -m unittest discover -s tests -v
```

The suite covers script registration, concurrency, session recovery, artifacts, audit records, failure dismissal, group execution, and scheduling. Windows batch execution tests are skipped on other platforms. Test scripts and execution data use temporary directories.

Browser checks require Node.js and an installed Chromium browser, with no npm dependencies:

```text
node tests/test_audit_scroll.js
node tests/test_automation_ui.js
node tests/test_workspace_ui.js
```

The checks automatically detect Edge or Chrome on Windows and common Chromium paths on Linux. Set `ACW_TEST_BROWSER` to an executable path if automatic detection is unavailable. They run the actual frontend in a temporary headless browser profile; automation and workspace actions use mock bridges and do not execute your scripts. The checks cover audit scrolling, group and schedule forms, job filtering, live-feed controls, and navigation.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
