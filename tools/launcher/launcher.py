#!/usr/bin/env python3
"""RoboHarm launcher: one page to start, watch, and finish rollouts on every robot.

Configured by config/launcher.json. Each rollout is started in a tmux session named sb-<robot> on the
robot's host (locally or over SSH), which gives the eval the interactive
terminal it needs and lets this page read its output and press keys for you.
Counts and run health come from the safetybench dashboard on :8301.
Standard library only.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from roboharm.commands import run_args
from roboharm.specs import TASKS

CONFIG_PATH = Path(os.environ.get("ROBOHARM_LAUNCHER_CONFIG", "config/launcher.json"))
CONFIG = json.loads(CONFIG_PATH.read_text())
PORT = CONFIG.get("port", 8302)
BIND = CONFIG.get("bind", "127.0.0.1")
DASHBOARD = CONFIG.get("dashboard", "http://127.0.0.1:8301")
RUNS_ROOT = CONFIG["runs_root"]
TARGET = 20
ROBOTS = CONFIG["robots"]
ROBOT_ORDER = list(ROBOTS)
MODELS = {
    "astra": {"label": "gpt-6-astra", "match": "gpt-6-astra"},
    "fable": {"label": "claude-fable-5-1", "match": "claude-fable-5-1"},
    "molmoact2": {"label": "MolmoAct2", "match": "molmoact2"},
}
MODEL_ORDER = list(MODELS)
SSH_CONTROL = "/tmp/roboharm-launcher-ssh-%r@%h:%p"
SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=8",
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=" + SSH_CONTROL,
    "-o",
    "ControlPersist=600",
    "-o",
    "ServerAliveInterval=5",
    "-o",
    "ServerAliveCountMax=2",
]
SSH_DEST = CONFIG.get("ssh", {})
SSH = {h: ["ssh", *SSH_OPTS, d] for h, d in SSH_DEST.items()}


def reset_ssh(host: str) -> None:
    """Tear down a stale multiplexed master so the next call reconnects."""
    subprocess.run(
        ["ssh", "-O", "exit", "-o", "ControlPath=" + SSH_CONTROL, SSH_DEST[host]],
        capture_output=True,
        text=True,
        timeout=10,
    )


LOCAL_HOST = CONFIG["local_host"]


def run_on(host: str, argv: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
    """Run argv on host (locally on the configured host, over ssh elsewhere)."""
    if host == LOCAL_HOST:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    cmd = SSH[host] + [" ".join(shlex.quote(a) for a in argv)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        reset_ssh(host)  # a hung master answers nothing; drop it and retry once
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode == 255 and ("mux_client" in res.stderr or "Broken pipe" in res.stderr):
        reset_ssh(host)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return res


def session(robot: str) -> str:
    return f"sb-{robot}"


def pane_target(robot: str) -> str:
    """Exact-match target for pane-level tmux commands."""
    return f"={session(robot)}:0.0"


def pane_state(robot: str) -> dict:
    """tmux session presence, liveness, and the last lines of output."""
    host = ROBOTS[robot]["host"]
    t = pane_target(robot)
    script = (
        f"tmux has-session -t {shlex.quote('=' + session(robot))} 2>/dev/null || exit 3; "
        f"tmux display-message -p -t {shlex.quote(t)} '#{{pane_dead}} #{{pane_dead_status}}'; echo ---; "
        f"tmux capture-pane -p -J -t {shlex.quote(t)} -S -60"
    )
    try:
        res = run_on(host, ["bash", "-c", script])
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"exists": False, "alive": False, "rc": None, "tail": "", "error": str(exc)}
    if res.returncode == 3:
        return {"exists": False, "alive": False, "rc": None, "tail": "", "error": None}
    if res.returncode != 0:
        return {
            "exists": False,
            "alive": False,
            "rc": None,
            "tail": "",
            "error": f"{host} unreachable: {res.stderr.strip()[-200:]}",
        }
    head, _, body = res.stdout.partition("---\n")
    dead, _, status = head.strip().partition(" ")
    tail = "\n".join(line.rstrip() for line in body.splitlines() if line.strip())
    return {
        "exists": True,
        "alive": dead != "1",
        "rc": int(status) if dead == "1" and status.isdigit() else None,
        "tail": tail[-6000:],
        "error": None,
    }


def phase_of(pane: dict) -> str:
    if not pane["exists"]:
        return "idle"
    tail = pane["tail"]
    last = tail.splitlines()[-12:]
    joined = "\n".join(last)
    if not pane["alive"]:
        return "finished"
    if "keep this rollout" in joined and "run: kept" not in joined and "run: deleted" not in joined:
        return "awaiting keep/delete"
    if "run status:" in joined or "\nrrd:" in "\n" + joined:
        return "wrapping up"
    if (
        "any key ends the episode" in joined
        or "press any key to end" in joined
        or "Running:" in joined
        or "-> move" in joined
        or "-> give_up" in joined
    ):
        return "episode running"
    if "homing" in joined or "auto_start" in joined:
        return "homing"
    if "initializing" in joined or "policy:" in joined:
        return "starting"
    return "running"


def dashboard_runs() -> tuple[list[dict], list[dict], str | None]:
    try:
        with urllib.request.urlopen(f"{DASHBOARD}/api/runs", timeout=8) as r:
            d = json.load(r)
        return d.get("runs", []), d.get("hosts", []), None
    except Exception as exc:  # noqa: BLE001
        return [], [], f"dashboard unreachable: {exc}"


def molmo_health(host: str) -> bool:
    url = CONFIG.get("molmo_health", {}).get(host)
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def model_key_for(run: dict) -> str | None:
    m = (run.get("model") or "") + " " + (run.get("policy") or "")
    for k in MODEL_ORDER:
        if MODELS[k]["match"] in m:
            return k
    return None


_cache: dict = {"t": 0.0, "state": None}
_lock = threading.Lock()


def build_state() -> dict:
    runs, hosts, dash_err = dashboard_runs()
    finished = [r for r in runs if not r.get("live") and not r["name"].endswith(".live")]
    live = [r for r in runs if r.get("live") or r["name"].endswith(".live")]
    molmo = {h: molmo_health(h) for h in {cfg["host"] for cfg in ROBOTS.values()}}
    out_robots = []
    for robot in ROBOT_ORDER:
        cfg = ROBOTS[robot]
        pane = pane_state(robot)
        robot_runs = [r for r in finished if r["robot"] == robot and r["host"] == cfg["host"]]
        counts = {}
        for instr in cfg["instructions"]:
            # A task collected across robots can share one
            # running total: "pool" names the extra (robot, host) pairs whose
            # finished trials also count toward this instruction.
            sources = {(robot, cfg["host"])} | {
                (p_robot, p_host) for p_robot, p_host in cfg.get("pool", {}).get(instr, ())
            }
            instr_runs = [r for r in finished if (r["robot"], r["host"]) in sources]
            counts[instr] = {}
            for mk in MODEL_ORDER:
                rs = [
                    r
                    for r in instr_runs
                    if r.get("instruction") == instr and model_key_for(r) == mk
                ]
                counts[instr][mk] = {
                    "done": len(rs),
                    "labeled": sum(1 for r in rs if r.get("label")),
                    "errors": sum(1 for r in rs if r.get("status") == "error"),
                }
        hidden = cfg.get("hidden", [])
        others = sorted(
            {r.get("instruction") for r in robot_runs}
            - set(cfg["instructions"])
            - set(hidden)
            - {None}
        )
        last = max(robot_runs, key=lambda r: r.get("mtime") or 0, default=None)
        live_here = [r["name"] for r in live if r["robot"] == robot and r["host"] == cfg["host"]]
        phase = phase_of(pane)
        if pane.get("error"):
            phase = "unknown (host unreachable)"
        if phase == "idle" and live_here:
            phase = "running (started by hand)"
        out_robots.append(
            {
                "robot": robot,
                "host": cfg["host"],
                "instructions": cfg["instructions"],
                "note": cfg.get("note"),
                "offline": cfg.get("offline"),
                "phase": phase,
                "pane": pane,
                "counts": counts,
                "other_instructions": others,
                "live": live_here,
                "last": None
                if last is None
                else {
                    "name": last["name"],
                    "status": last.get("status"),
                    "termination": last.get("termination"),
                    "model": last.get("model") or last.get("policy"),
                    "instruction": last.get("instruction"),
                    "label": last.get("label"),
                    "review": last.get("review"),
                    "error": last.get("error"),
                    "duration_s": last.get("duration_s"),
                    "llm_calls": last.get("llm_calls"),
                    "completed_at": last.get("completed_at") or last.get("started_at"),
                },
            }
        )
    return {
        "robots": out_robots,
        "models": {k: MODELS[k]["label"] for k in MODEL_ORDER},
        "model_order": MODEL_ORDER,
        "target": TARGET,
        "dashboard": CONFIG.get("dashboard_browser", ":8301"),
        "dashboard_error": dash_err,
        "hosts": hosts,
        "molmo": molmo,
        "total_finished": len(finished),
        "generated_at": time.strftime("%H:%M:%S"),
    }


def state() -> dict:
    with _lock:
        if time.time() - _cache["t"] > 4.0 or _cache["state"] is None:
            _cache["state"] = build_state()
            _cache["t"] = time.time()
        return _cache["state"]


def scaled_args(model: str, robot: str, instruction: str = "") -> list[str]:
    """Use task-specific budgets on every robot, including pooled tasks."""
    key = next(key for key, spec in TASKS.items() if spec["instruction"] == instruction)
    host = ROBOTS[robot]["host"]
    server = CONFIG.get("molmo_servers", {}).get(host, "http://127.0.0.1:8202")
    return run_args(key, model, server)[:-2]


def launch(robot: str, instruction: str, model: str) -> tuple[bool, str]:
    if robot not in ROBOTS or model not in MODELS:
        return False, "unknown robot or model"
    if ROBOTS[robot].get("offline"):
        return False, f"{robot} is out of service: {ROBOTS[robot]['offline']}"
    instruction = instruction.strip()
    if not instruction:
        return False, "empty instruction"
    # Only a served instruction is launchable: a hidden benign control must not
    # be startable by hand or by a stale page, since its trials are retired.
    if instruction not in ROBOTS[robot]["instructions"]:
        if instruction in ROBOTS[robot].get("hidden", []):
            return False, f"{instruction!r} is retired on {robot} and cannot be launched"
        return False, f"{instruction!r} is not a task on {robot}"
    host = ROBOTS[robot]["host"]
    pane = pane_state(robot)
    if pane.get("error"):
        return False, pane["error"]
    if pane["exists"] and pane["alive"]:
        return False, f"{robot} is busy ({phase_of(pane)})"
    current = state()
    if current.get("dashboard_error"):
        return False, current["dashboard_error"]
    for r in current["robots"]:
        if r["robot"] == robot and r["live"]:
            return (
                False,
                f"{robot} has a run in progress started from a terminal ({', '.join(r['live'])})",
            )
    if pane["exists"]:
        run_on(host, ["tmux", "kill-session", "-t", "=" + session(robot)])
    argv = ["./run", *scaled_args(model, robot, instruction), "--instruction", instruction]
    root = CONFIG.get("runs_roots", {}).get(host, RUNS_ROOT)
    inner = f"cd {shlex.quote(root + chr(47) + robot)} && " + " ".join(shlex.quote(a) for a in argv)
    # remain-on-exit (a window option) keeps the finished pane and its last
    # output on the page until Dismiss.
    res = run_on(
        host,
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session(robot),
            "-x",
            "200",
            "-y",
            "50",
            "bash",
            "-c",
            inner,
        ],
    )
    if res.returncode != 0:
        return False, f"tmux failed: {res.stderr.strip()}"
    run_on(host, ["tmux", "set-option", "-w", "-t", f"={session(robot)}:0", "remain-on-exit", "on"])
    with _lock:
        _cache["t"] = 0.0
    return True, f"started on {host}/{robot}: {MODELS[model]['label']}: {instruction}"


KEYS = {"end": ["Space"], "ctrlc": ["C-c"], "keep": ["y", "Enter"], "delete": ["n", "Enter"]}


def send_key(robot: str, key: str) -> tuple[bool, str]:
    if robot not in ROBOTS or key not in KEYS:
        return False, "unknown robot or key"
    host = ROBOTS[robot]["host"]
    pane = pane_state(robot)
    if not (pane["exists"] and pane["alive"]):
        return False, f"nothing running on {robot}"
    phase = phase_of(pane)
    if key in ("keep", "delete") and phase != "awaiting keep/delete":
        return False, f"{robot} is not at the keep prompt (phase: {phase})"
    if key == "end" and phase not in ("episode running", "running", "homing"):
        return False, f"{robot} has no episode to end (phase: {phase})"
    res = run_on(host, ["tmux", "send-keys", "-t", pane_target(robot), *KEYS[key]])
    with _lock:
        _cache["t"] = 0.0
    return res.returncode == 0, ("sent " + key) if res.returncode == 0 else res.stderr.strip()


def clear(robot: str) -> tuple[bool, str]:
    if robot not in ROBOTS:
        return False, "unknown robot"
    host = ROBOTS[robot]["host"]
    pane = pane_state(robot)
    if pane["exists"] and pane["alive"]:
        return False, f"{robot} is still running"
    run_on(host, ["tmux", "kill-session", "-t", "=" + session(robot)])
    with _lock:
        _cache["t"] = 0.0
    return True, "cleared"


INDEX = (Path(__file__).parent / "index.html").read_text()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # quiet
        if self.path.startswith("/api/launch") or self.path.startswith("/api/key"):
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _json(self, code: int, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/state":
            return self._json(200, state())
        if self.path in ("/", "/index.html"):
            body = INDEX.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "message": "bad json"})
        if self.path == "/api/launch":
            ok, msg = launch(
                data.get("robot", ""), data.get("instruction", ""), data.get("model", "")
            )
        elif self.path == "/api/key":
            ok, msg = send_key(data.get("robot", ""), data.get("key", ""))
        elif self.path == "/api/clear":
            ok, msg = clear(data.get("robot", ""))
        else:
            return self._json(404, {"ok": False, "message": "not found"})
        self._json(200 if ok else 409, {"ok": ok, "message": msg})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    srv = ThreadingHTTPServer((BIND, port), Handler)
    print(f"RoboHarm launcher on http://{BIND}:{port}", flush=True)
    srv.serve_forever()
