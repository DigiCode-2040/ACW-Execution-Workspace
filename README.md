# ACW — Advanced Control Workspace

ACW is a local-only desktop workspace for running trusted Python scripts, composing small automation workflows, and keeping a durable record of each execution. It is designed to stay on the user’s machine: the app uses a pywebview JS/Python bridge, an allowlisted script catalog, and local filesystem persistence. No server, no remote execution, and no shell-based command evaluation are required.

This project is intended for local automation and personal workflow orchestration: organizing files, validating outputs, quality checks, data prep, and scripted tasks you explicitly trust.

## What ACW includes

- Approved script catalog with metadata discovery from Python headers
- Typed argument schemas for safer runtime validation and UI form generation
- Terminal argument validation and autocomplete for schematized scripts
- Local workflow DAG orchestration with sequential, parallel, conditional, and fan-out/fan-in steps
- Parent/child job tracking for multi-step workflows
- Run provenance: resolved args, script hashes, versions, input hashes, artifact hashes, environment snapshot, and event timeline
- History, comparison, and visual insights for runtime and quality trends
- Canvas-style job graph with workflow edges and stage timing/Gantt-style views
- Persistent sessions and run snapshots under the project `runs/` and `sessions/` directories
- Safe artifact reporting and Open/Reveal actions restricted to allowed project or configured output directories

## Requirements

- Python 3.10+
- Desktop OS with a supported pywebview renderer (Windows, macOS, Linux)
- Local dependencies from `requirements.txt`

## Run locally

```bash
python -m venv .venv
```

Activate it:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Then:

```bash
python -m pip install -r requirements.txt
python main.py
```

This opens the desktop window. Opening `ui/index.html` directly in a browser does not establish the pywebview bridge and will not connect to the local Python runtime.

## Core workflow

The app is driven by an allowlisted catalog of trusted scripts:

- scripts are discovered from the `scripts/` directory and/or registered via the UI
- each script may declare metadata, validation schema, and output directories
- jobs are queued and run under a configurable concurrency limit
- jobs can be launched individually or as part of a workflow
- all runs are recorded locally under `runs/`

## Console and UI commands

Common terminal actions include:

```text
help
catalog
catalog reload
run <script> [arguments...]
workflow <name>
run-workflow <name>
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

The UI also exposes:

- Run Selected and workflow launch controls
- generated form inputs for schematized scripts
- file/folder pickers for file and directory arguments
- Canvas view for jobs and workflow nodes/edges
- run insights panel for trends, diffing, and bottleneck detection

## Script discovery and metadata

Scripts can declare metadata in their first ~20 lines, for example:

```python
# ACW_COMMAND: organize
# ACW_LABEL: Organize Downloads
# ACW_DESCRIPTION: Sort files in ~/Downloads by type
# ACW_OUTPUT_DIR: ./output
```

Supported metadata includes:

- `ACW_COMMAND`: unique command name used in the catalog and CLI
- `ACW_DESCRIPTION`: short description shown to users
- `ACW_LABEL`: friendly display name in the UI
- `ACW_OUTPUT_DIR`: declared output directory or project-relative path allowed for artifact actions

Scripts with valid metadata are discovered automatically. Catalog reload refreshes the registry without restarting the app.

## Input schemas

ACW supports a formal input schema for commands. This can be declared with a JSON object in the source header or in a companion schema file; the exact format is intentionally lightweight and local. The schema can describe:

- parameter name and display label
- type: `string`, `path`, `file`, `bool`, `int`, `enum`
- required vs optional
- default values
- validation rules like min/max, pattern, and enum choices
- UI hints such as file picker, directory picker, multiline, or secret input
- human-readable descriptions

Schema validation occurs before the subprocess is launched. This keeps the catalog safer, makes generated forms easier to use, and helps terminal runs validate arguments consistently.

Example header metadata for a schematized script:

```python
# ACW_COMMAND: organize
# ACW_DESCRIPTION: Organize files in a target directory
# ACW_INPUTS: {
#   "type": "object",
#   "properties": {
#     "target": {"type": "path", "label": "Target folder", "description": "Directory to scan", "hint": "directory", "required": true},
#     "dry_run": {"type": "bool", "default": true, "description": "List what would change without moving files"}
#   },
#   "required": ["target"]
# }
```

Scripts without schemas can still be run as free-form commands, but schematized scripts gain validation, form generation, better catalog metadata, and safer reuse.

## Workflow orchestration

Workflows are local DAG definitions stored as JSON or YAML under `workflows/` and reference approved catalog commands by name.

A workflow can include:

- sequential steps
- parallel execution with respect to `max_concurrent`
- conditional branches and recovery nodes
- fan-out/fan-in patterns
- artifact and output passing from one step to another
- parent job + child job records for traceability

Example workflow structure:

```json
{
  "name": "demo-quality",
  "steps": [
    {"name": "scan", "command": "organize", "params": {"target": "./downloads", "dry_run": true}},
    {"name": "quality", "command": "quality_check", "depends_on": ["scan"]}
  ]
}
```

The workflow runner creates a parent job linked to child jobs, records dependency state, and preserves partial workflow metadata if interrupted.

## Provenance and run analytics

Every run capture is persisted locally under `runs/`.

Key provenance details include:

- exact script hash and version
- resolved argument values after schema validation
- environment snapshot (Python version, platform, key package versions where declared)
- input file hashes for provided paths
- artifact hashes and output references
- structured event timeline and stage timings
- CPU/memory sampling over time when available

The app also provides run-insights views for:

- runtime and quality trends
- per-stage timing and bottleneck detection
- run-to-run diffing
- exportable provenance JSON reports
- visual comparison heatmaps and stage timing views

## Sessions and history

ACW records execution state locally so it can resume and inspect previous work:

- `runs/<job-id>.json`: per-run snapshot
- `runs/history.jsonl`: compact append-only historical index
- `runs/last_session.json`: auto-saved current state
- `sessions/`: named saved workspace states

The app tracks sessions, restores interrupted jobs, and supports history inspection and run comparisons by script name.

## Security model

ACW is intentionally local and allowlisted:

- only approved scripts may be run
- subprocesses use a direct Python spawn with no shell expansion
- terminal parsing is local and argument validation happens before launch
- artifact access is restricted to the project root and configured output directories
- secrets are masked/redacted in persisted arguments, event history, and workflow snapshots

This is a desktop automation tool, not a general-purpose remote agent platform. Only add scripts you trust.

## Project structure

```text
ACW-Execution-Workspace/
├── acw/
│   ├── __init__.py
│   ├── api.py
│   └── engine.py
├── ui/
│   ├── index.html
│   └── app.js
├── scripts/
│   ├── demo_pipeline.py
│   ├── quality_check.py
│   └── organize_downloads.py
├── workflows/
│   └── demo-quality.json
├── tests/
│   └── test_engine.py
├── main.py
├── requirements.txt
├── README.md
├── LICENSE
├── runs/
│   ├── history.jsonl
│   ├── last_session.json
│   └── <job-id>.json
├── sessions/
└── screenshots/
```

## Limitations

- This is a local desktop app, not a distributed scheduler or cloud system.
- Workflow execution is still intentionally local and bounded by the machine on which ACW is running.
- Resource and provenance telemetry are best-effort and useful for local optimization, but they are not a substitute for full production observability.
- The safest operating model is to keep the catalog tightly controlled and review any new script before approving it.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
