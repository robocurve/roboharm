# Source provenance

- The task-package and documentation layout follows
  [StationeryBench](https://github.com/robocurve/stationerybench) (MIT).
  RoboHarm's task definitions and analysis code are newly written for this repository.
- `tools/dashboard` is derived from the Robocurve experiment dashboard (SafetyBench
  fork) as deployed on 2026-09-18. Its base commit was
  `d9e5a5a133d110a67408bcd968a0861b37c0d92f`; the deployed working tree also
  contained later uncommitted changes.
- `tools/launcher` is adapted from the deployed SafetyBench launcher on that date.
  Hardcoded lab addresses and paths were replaced with configuration. It now uses
  the package's canonical per-task argument builder.
- Setup images are frames from the RoboHarm experiment footage. Their run IDs
  are recorded in `docs/assets/provenance.json`. No human demonstration videos
  are claimed or synthesized.
- Inspect Robots, its adapters, and MolmoAct2 are external dependencies, not
  vendored here; their own licenses and access conditions apply.

Unless noted otherwise above, everything in this repository, including the
recovered dashboard and launcher sources and the setup images, is released under the
[Creative Commons Attribution-NonCommercial 4.0 International](LICENSE) license (CC BY-NC 4.0).
