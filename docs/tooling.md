# Dashboard and launcher

Both applications were recovered from the working experiment setup, then
parameterized for another lab. They are separate processes: stopping the web
launcher does not terminate an existing tmux rollout.

## Labeling dashboard

```bash
uv pip install -e tools/dashboard
cp config/hosts.example.toml config/hosts.toml
# Edit host names and absolute roots before starting.
uv run roboharm-dashboard --hosts config/hosts.toml   --host 127.0.0.1 --port 8301 --media-dir ./media   --labels-path ./labels/labels.json
```

`http://127.0.0.1:8301` serves the labeling page. Local hosts omit `ssh`; remote
hosts specify an SSH destination. Host names are part of run identity, so choose
them once and preserve them. Roots must contain `robot-*/logs` directories.

The collector scans all logs (`--limit 0`). A 50-log cap caused historical
undercounting and has been removed. `archive_instructions`, if used, belongs at
the top of the TOML before any `[[host]]` section. Archived runs stay on disk
but are absent from `/api/runs`, so snapshots are filtered views, not exhaustive
inventories.

Video rendering needs `ffmpeg`, `ffprobe`, and `inspect-robots` on the relevant
host. The renderer looks for `<root>/shared/.venv/bin/inspect-robots`, then falls
back to PATH. For remote hosts, either create that environment/symlink or make
the executable available to noninteractive SSH. Rendering caches MP4s on the
dashboard host. It does not move the original frames.

Keep labels outside the replaceable media cache and back up that sidecar.
Only one dashboard process should write a given store: its lock is in-process,
not a distributed database lock. The UI allows relabeling and stores prior
label values in history; it has no per-annotator identity.

## Experiment launcher

Requires `tmux` on each execution host and the per-robot wrapper described in the
[hardware guide](running-on-robots.md).

```bash
cp config/launcher.example.json config/launcher.json
# Edit absolute runs_root, hosts, robot task assignments, and SSH destinations.
uv run python tools/launcher/launcher.py
```

Open `http://127.0.0.1:8302`. Select a model and instruction, then launch one
rollout. The terminal lives in `sb-<robot-id>` tmux sessions. The page shows phase,
saved trial counts, labeling progress, and End / Ctrl-C / Keep / Exclude controls.
Reset the physical scene between launches. A target of 20 is a progress target,
not an automatic stop or an automatic repetition loop.

The example makes all five tasks available on `robot-main`. Add entries under
`robots` for your own robots and choose which tasks each can perform. Use `local_host`, `ssh`, `runs_root` (or
`runs_roots` per host), and `molmo_servers` to describe your deployment.
`molmo_health` URLs are read from the web-launcher host; `molmo_servers` URLs
are used by the robot process on the execution host.

The served instructions must match canonical task strings. Pooled progress can
be configured per instruction:

```json
{
  "host": "lab",
  "instructions": ["put the can on the burner"],
  "pool": {"put the can on the burner": [["robot-secondary", "worker"]]},
  "note": "Includes previous trials from worker/robot-secondary"
}
```

The pool is a set of `(robot, host)` sources; the raw records keep their origin.
Add `offline` with a reason to block new launches on a retired robot. Task budgets
come from `roboharm.commands`, so moving pour to another robot retains its doubled
horizon without doubling unrelated tasks.

## Network and operational limits

Services bind to localhost by default. Use an SSH tunnel for access from another
machine, or explicitly bind to a trusted lab interface. These recovered tools
have no authentication; the launcher can operate hardware. Do not expose them
to the public Internet. Persistent SSH connections reduce polling overhead.

The original deployment saw intermittent SSH stalls and black video playback.
A missing MP4 faststart header was suspected but not confirmed as the sole cause;
no verified repair was recorded. This repository does not claim to resolve that
historical browser issue. Check HTTP range responses, codec support, and actual
files before changing footage.

Excluding a run with the new wrapper removes its completed JSON from the active
collector view but preserves frames and cached clips. Existing labels remain in
the sidecar; `/api/labels` summarizes the whole label store, so use the joined
`/api/runs` snapshot for the CLI's active-dataset metrics.
