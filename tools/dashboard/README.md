# RoboHarm labeling dashboard

Recovered from the deployed SafetyBench fork of the Robocurve experiment dashboard,
September 18, 2026. Includes five labels, inline annotation, synchronized camera
review, transcript views, CSV export, unlimited log scanning, and SSH collection.
See [the deployment guide](../../docs/tooling.md). No experiment data is bundled.

```bash
uv pip install -e tools/dashboard
roboharm-dashboard --hosts config/hosts.toml --media-dir ./media --labels-path ./labels/labels.json
```

Keep the label store outside the media cache. Run exactly one dashboard process
per label store. The service has no authentication; it binds to localhost by default.
