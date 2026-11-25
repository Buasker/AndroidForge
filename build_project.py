#!/usr/bin/env python3
"""AndroidForge build runner with clearer diagnostics and preserved logs."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path
from typing import Any

def load_json(value: str) -> Any:
    path = Path(value)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else json.loads(value)

def run(cmd: list[str], cwd: Path, log_file: Path, env: dict[str, str] | None = None):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Running: {' '.join(cmd)}", flush=True)
    print(f"cwd: {cwd}\nlog: {log_file}", flush=True)
    full_env = os.environ.copy()
    if env: full_env.update(env)
    try:
        with log_file.open("w", encoding="utf-8", errors="replace") as f:
            f.write(f"$ {' '.join(cmd)}\n# cwd: {cwd}\n\n")
            proc = subprocess.Popen(cmd, cwd=str(cwd), env=full_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1)
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line); sys.stdout.flush(); f.write(line)
            rc = proc.wait()
    except FileNotFoundError as e:
        msg = f"COMMAND NOT FOUND: {e}"
        print(msg, flush=True); log_file.write_text(msg + "\n", encoding="utf-8")
        return 127, str(log_file)
    except Exception as e:
        msg = f"FAILED TO START COMMAND: {type(e).__name__}: {e}"
        print(msg, flush=True); log_file.write_text(msg + "\n", encoding="utf-8")
        return 1, str(log_file)
    print(f"=== Exit {rc}", flush=True)
    return rc, str(log_file)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--detect", required=True)
    ap.add_argument("--toolchain", required=True)
    ap.add_argument("--variant", default="auto", choices=["auto","debug","release","bundle"])
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--output", default=None)
    a = ap.parse_args()

    detect, toolchain = load_json(a.detect), load_json(a.toolchain)
    root = Path(a.root).resolve()
    logs = Path(a.log_dir).resolve() if a.log_dir else root.parent / "androidforge-logs"
    logs.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        print(f"ERROR: project root does not exist: {root}", flush=True)
        return 2

    project_type = detect.get("project_type", "unknown")
    needs_flutter = bool(toolchain.get("needs_flutter", False))
    use_wrapper = bool(toolchain.get("use_wrapper", False))
    commands = toolchain.get("build_commands", []) or []
    prep = toolchain.get("prep_commands", []) or []

    if a.variant != "auto":
        selected = []
        for cmd in commands:
            s = " ".join(cmd).lower()
            if ((a.variant == "debug" and "debug" in s) or
                (a.variant == "release" and "release" in s) or
                (a.variant == "bundle" and "bundle" in s)):
                selected.append(cmd)
        if selected: commands = selected

    print("=== AndroidForge diagnostics ===", flush=True)
    print(f"Project root: {root}", flush=True)
    print(f"Detected type: {project_type}", flush=True)
    print(f"Flutter required: {needs_flutter}", flush=True)
    print(f"pubspec.yaml at root: {(root/'pubspec.yaml').is_file()}", flush=True)
    print(f"gradlew at root: {(root/'gradlew').is_file()}", flush=True)
    print(f"Preparation commands: {prep}", flush=True)
    print(f"Build commands: {commands}", flush=True)

    prep_results = []
    for i, cmd in enumerate(prep, 1):
        if not isinstance(cmd, list) or not cmd:
            prep_results.append({"command": cmd, "exit_code": 2, "log_file": None})
            print(f"ERROR: invalid preparation command: {cmd!r}", flush=True)
            continue
        rc, log = run(cmd, root, logs / f"prep-{i}-{int(time.time())}.log")
        prep_results.append({"command": cmd, "exit_code": rc, "log_file": log})
        if rc:
            print(f"PREPARATION FAILED (exit {rc}): {' '.join(cmd)}", flush=True)
            print(f"Preparation log: {log}", flush=True)

    attempts, success, last_rc, last_log, successful = [], False, 1, None, None
    for i, cmd in enumerate(commands, 1):
        if not isinstance(cmd, list) or not cmd:
            attempts.append({"command": cmd, "exit_code": 2, "log_file": None})
            continue
        actual = list(cmd)
        if use_wrapper and actual[0].endswith("gradlew"):
            wrapper = Path(actual[0])
            if not wrapper.is_absolute(): wrapper = root / wrapper
            if wrapper.is_file():
                try: wrapper.chmod(wrapper.stat().st_mode | 0o111)
                except OSError: pass
                actual = ["/bin/sh", str(wrapper)] + actual[1:]
        rc, log = run(actual, root, logs / f"build-{i}-{int(time.time())}.log")
        attempts.append({"command": actual, "exit_code": rc, "log_file": log})
        last_rc, last_log = rc, log
        if rc == 0:
            success, successful = True, actual
            break

    if not commands:
        print("ERROR: toolchain.json contains no build_commands.", flush=True)
        last_rc = 2

    result = {
        "project_root": str(root), "project_type": project_type,
        "needs_flutter": needs_flutter,
        "pubspec_at_project_root": (root/"pubspec.yaml").is_file(),
        "build_succeeded": success, "successful_command": successful,
        "exit_code": last_rc, "log_file": last_log,
        "prep_results": prep_results, "build_attempts": attempts
    }
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if a.output: Path(a.output).write_text(rendered + "\n", encoding="utf-8")
    print("\n=== BUILD RESULT ===\n" + rendered, flush=True)

    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"build_succeeded={'true' if success else 'false'}\n")
            f.write(f"exit_code={last_rc}\n")
            f.write(f"log_file={last_log or ''}\n")
            if successful: f.write(f"successful_command={' '.join(successful)}\n")
            f.write("json<<EOF\n" + json.dumps(result, ensure_ascii=False) + "\nEOF\n")
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
