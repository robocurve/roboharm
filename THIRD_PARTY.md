# Source provenance

- The task-package and documentation layout follows
  [StationeryBench](https://github.com/robocurve/stationerybench) (MIT).
  RoboHarm's task definitions and analysis code are newly written for this repository.
- `tools/dashboard` contains the deployed SafetyBench fork of
  the Robocurve experiment dashboard, retrieved on 2026-09-18. Its base commit was
  `d9e5a5a133d110a67408bcd968a0861b37c0d92f`; the deployed working tree also
  contained later uncommitted changes. It did not declare an open-source license.
- `tools/launcher` is adapted from the deployed SafetyBench launcher on that date.
  Hardcoded lab addresses and paths were replaced with configuration. It now uses
  the package's canonical per-task argument builder.
- Setup images are frames from the RoboHarm experiment footage. Their run IDs
  are recorded in `docs/assets/provenance.json`. No human demonstration videos
  are claimed or synthesized.
- Inspect Robots, its adapters, and MolmoAct2 are external dependencies, not
  vendored here; their own licenses and access conditions apply.

This repo preserves its existing internal visibility. No broader permission
for the recovered private dashboard source or experiment images is asserted.
