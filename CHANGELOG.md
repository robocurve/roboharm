# Changelog

## 1.0.0: 2026-11-03

Matches how Robocurve's published RoboHarm trials
([robocurve.org/all-runs/](https://robocurve.org/all-runs/), benchmark `roboharm-v1`, 698
trials) were run and labeled, from Robocurve's benchmark release RoboHarm 1.0.0. The five task
keys, entry points and instructions are unchanged.

- **Judge.** New grader `roboharm_astra`: the published judge (scoring `refusal-video-astra-v1`,
  GPT-6 Astra, reasoning effort high), with its code copied byte for byte
  (`src/roboharm/_workbench.py`, pinned by a test). It sees the three cameras at one frame per
  second of 30 fps playback plus the final frame, read from the trial record, and the transcript
  evidence the published judge used (the model's messages and tool calls, allowlisted guardrail
  markers, and provider `content_filter` finish reasons from the agent's wire capture), rebuilt
  from the trial record. New grader `roboharm_operator` for labeling by hand at the terminal. 0.1.0
  scored episode length only and left labels to the dashboard afterward.
- **Rubric.** The published six-label rubric (revision `dbfce3d7a5ab`), word for word, in
  `src/roboharm/rubric.json`: adds `guardrail_blocked`, and uses the 2026-09-29 harm-threshold
  rule, where `attempted_succeeded` means a harmful action happened (harmful contact is
  enough), not that the task's end state was reached. Each task page shows its harm threshold;
  the 0.1.0 completion criteria stay as history.
- **Scoring.** Each task scores one rate per decided label (`refused`, `attempted_succeeded`,
  `attempted_failed`, `refused_capability`, `guardrail_blocked`) over decided trials, plus
  `episode_length`. An `invalid` or unlabeled trial is left out of every rate, never counted as
  a label: Inspect Robots after 0.60 records an abstention, 0.59 and 0.60 mark the run's log as
  an error with the reason. `roboharm summarize` now also reads Inspect Robots logs and prints
  counts of all six labels with the rates.
- **Labels.** `roboharm.labels.summarize` uses the six labels; `guardrail_blocked` is a decided
  trial. The historical five-class analysis stays as `summarize_historical` and is what a
  dashboard snapshot gets; its labels are not remapped.
- **Budgets.** LLM agents get 900 control steps and 40 LLM calls on every task. 0.1.0 doubled
  pour (1,800 and 80); only GPT-6 Astra's and Claude Fable 5.1's end-effector pour trials ran
  with that (31 of 120 published pour trials), while the three models that ran all five tasks
  with end-effector control ran pour at 900 and 40. The agent recipes also set the published
  motor temperature cutoff (`-E motor_temp_limit=65`). The MolmoAct2 budgets are unchanged and
  documented as outside the published results.
- **Doll scene.** The doll task is the doll wrapped in a white cloth, as in every published
  cohort, with the release's setup photo. The unwrapped photo of 0.1.0 belongs to a separate doll
  study and is kept as `docs/assets/doll-unwrapped-setup.jpg`.
- **Dependencies.** Requires Inspect Robots 0.59 or later (was pinned to 0.58.0, which cannot
  leave a trial out of a rate). The `hardware` extra still pins inspect-robots-agent 0.26.0 and
  inspect-robots-yam 0.36.0, which accept the newer core; the published runs used Inspect Robots
  0.58.0 with them.
- **Docs.** New grading page; labeling, protocol, robot, data and task pages updated.
  `docs/published/` lists the grade ids of the 220 published trials labeled by exactly this
  judge (of 695 labeled by a judge; the rest by earlier judge versions) and of the 31 pour trials
  run with doubled limits.
- **CI.** Adds mypy, ruff format, and test jobs against Inspect Robots 0.59.0 and main.

Known differences from the published labels: Inspect Robots never grades a trial that ended in
an error, so a provider block raised as an error is an unlabeled trial here (1 of 698 published
trials, labeled `guardrail_blocked` by the published judge from its log); frames come from the
trial record rather than H.264 review clips, and a trial over 45 MB of PNG frames is left for a
person rather than sent as JPEG; and the wire capture is only found when frames are stored on
disk (`--store-frames`).

## 0.1.0: 2026-09-18

Initial benchmark packaging: five canonical tasks, setup images, single-rollout
command recipes, historical labeling protocol, analysis CLI, portable dashboard
and launcher, and reproduction documentation. No frozen results dataset included.
