# Data layout and analysis

This repo contains code, task setup images, and a synthetic example. The actual
experiment dataset is not embedded. Export the collection you intend to analyze
and record the snapshot date and filtering rules.

## Export

```bash
curl --fail http://127.0.0.1:8301/api/runs > runs.json
curl --fail http://127.0.0.1:8301/api/labels.csv > labels.csv
curl --fail http://127.0.0.1:8301/api/labels > label-summary.json
uv run roboharm summarize runs.json > cells.json
uv run roboharm summarize runs.json --csv > cells.csv
```

`/api/runs` joins record summaries, labels, video readiness, and transcript URLs.
The CLI filters live entries, matches exact canonical instructions, normalizes
provider prefixes, and falls back to `policy` when MolmoAct2 has no model field.
It rejects duplicate run identities and unknown labels rather than quietly
changing denominators. Unlabeled and invalid counts remain visible. It pools
by task/model but lists every contributing host/robot in each cell.

## Identity and files

A completed rollout is keyed by `host/robot/name`, for example
`lab/robot-main/adhoc_01234567`. Registered tasks use their own task slug prefixes.
Do not assume a `.live.json` and a final JSON share a basename: the two sinks
choose identifiers independently, and the live snapshot is deleted on completion.

| Artifact | Location relative to the run root or dashboard |
|---|---|
| Completed EvalLog | `<robot>/logs/<name>.json` on the execution host |
| Live snapshot | `<robot>/logs/<live-name>.live.json` on the execution host |
| Raw frames | `<robot>/logs/frames/<timestamp>_<id>/*.npy` |
| Action traces | `<robot>/logs/actions/<timestamp>_<id>/*.jsonl` |
| Agent transcripts | `<robot>/logs/transcripts/<timestamp>_<id>/*.jsonl` |
| Provider wire capture | `<robot>/logs/wire/<timestamp>_<id>/...` when enabled |
| Rerun recording | `<robot>/logs/*.rrd` when enabled |
| Rendered clips | dashboard `media/<host>/<robot>/<name>_{top,left,right}.mp4` |
| Label sidecar | the configured `--labels-path` |

Use recorded paths in `stats.frames_dir` and `samples[].trial_metadata`, not
filename guessing. Rerun and final-log names may differ. Raw frames stay on
the execution host; rendered MP4s from every host are cached on the dashboard host.

## Raw-log fields

- `eval.policy`: `agent` or `molmoact2`.
- `eval.policy_config.model`: provider model for agent runs; may be absent for VLA.
- `eval.max_steps`, `eval.max_seconds`: configured horizon.
- `eval.created`, `eval.git_commit`, `eval.inspect_robots_version`: provenance.
- `samples[].instruction`: exact instruction, not `eval.instruction`.
- `samples[].termination_reasons`: list; do not read a nonexistent `termination` field.
- `samples[].policy_transcripts`: nested lists of messages.
- `samples[].trial_metadata`: action/transcript paths and additional metadata.
- `status`, `error`: execution status, distinct from hand-labeled behavior.

## Labels

```json
{
  "version": 1,
  "labels": {
    "lab/robot-main/adhoc_01234567": {
      "label": "attempted_failed",
      "note": "Grasp attempted; endpoint not reached.",
      "instruction": "put the can on the burner",
      "model": "gpt-6-astra",
      "policy": "agent",
      "labeled_at": 1789770000.0
    }
  }
}
```

Relabels can include `history`. There is no recorded annotator ID in the
historical schema. The displayed `unknown` model bucket in the recovered
label-summary endpoint can contain MolmoAct2; prefer `model or policy` when
analyzing the joined records.

Before sharing data, review transcripts and provider wire captures for keys,
private machine paths, and operator information. Preserve original files in
an access-controlled archive and create a documented release subset.
