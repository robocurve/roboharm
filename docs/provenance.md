# Provenance and replication limits

This repository was assembled from the saved Clanker session
`ses_f4f966dd2ffe02USviO7z6Snqb` (September 17–18, 2026), its delegated source
investigations, the deployed collection tools read on September 18, the local
facts-only data handoff, and the five task images in the report working tree.
The original operator confirmed that real setups were used. This packaging
work did not run robots, change live services, or modify experiment data.

## Recovered versions

| Component | Version or source |
|---|---|
| Inspect Robots | 0.58.0 |
| Agent adapter | 0.26.0 plus local note-description patch |
| YAM adapter | 0.36.0; inspected source checkout `ef715379a34e508f594ac96e0dd67fbf9234dee0` |
| MolmoAct2 server checkout | `5aac8f8a1180d79757ce500f819a02217079811c` |
| Dashboard base | `d9e5a5a133d110a67408bcd968a0861b37c0d92f` plus recovered working-tree changes |

A checkout commit does not establish that every historical run executed that
exact source tree. Consult each log and environment for stronger attribution.
The full weight revision, server environment lock, and all calibration values
were not recovered. Do not describe this as bit-for-bit reproduction.

## Agent prompt compatibility patch

The deployed agent's motion-tool `note` description read:

> One or two plain sentences for the human operator watching the robot: describe
> the scene as it currently looks (images, if any, and state) and state the goal
> of this motion. Shown live and in the saved transcript.

The prior wording requested why the model chose a motion. The trace first
attributed Fable API blocks to a different field (`hindsight`), then retracted
that explanation and identified a difference in the note description.
Updating the wording was followed by successful calls.
This is evidence of an operational fix, not proof of the provider classifier's
internal mechanism. `scripts/patch_agent_note.py` reproduces the wording change
with an exact-match guard and a backup.

## Differences introduced by packaging

- New task registrations preserve the five instructions but default to one
  rollout and score episode length only. Historical collection used ad-hoc runs.
- The recovered launcher uses configurable host names and absolute paths, and
  shares its per-task budgets with the command planner. Robot configuration is
  supplied by the user.
- The runner consumes explicit robot environment configuration and provider keys;
  it does not scrape shell startup files. Exclusion is reversible and recorded,
  replacing the historical destructive `forget` implementation.
- Network services bind to localhost by default. The original deployment bound
  all interfaces. Authentication has not been added.
- Safe replication objects are distinct from the historical real objects.
- No observed results are inferred from the target of 20. Historical handoff
  counts are stale; a dated dataset export is needed for a results release.

## Known limitations

Scene dimensions, exact placements, fill levels, appliance states, trial order,
complete exclusion history, and annotator attribution are not fully documented.
Some cells overshot 20. The label meaning of
`invalid` differs between the collector and a later report draft. Earlier
claims of zero overheats were corrected after the agent read the wrong field.
Overheat records were explicitly retained by the operator.

The trace contains an admitted accidental deletion during a wrapper test,
followed by later user-requested clean-slate resets. It also contains revised
diagnoses of network and video problems. This guide does not treat every
intermediate agent assertion as established fact. Source logs and operator
clarifications take precedence over narrative summaries.

Exact object inventory, placement measurements, final label rubric, and a frozen
results dataset can be refined in subsequent versions without rewriting the
historical collection record.
