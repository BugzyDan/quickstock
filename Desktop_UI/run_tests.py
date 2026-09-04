#!/usr/bin/env python3
"""Small fallback runner for the pytest-style desktop tests.

Use pytest when it is installed. This runner exists so production checks can
still exercise the current lightweight test functions in minimal environments.
"""

import importlib.util
import inspect
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = PROJECT_ROOT / "Desktop_UI"
TEST_ROOT = DESKTOP_ROOT / "tests"

for path in (PROJECT_ROOT, DESKTOP_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


class MonkeyPatch:
    def __init__(self):
        self._env = []
        self._attrs = []

    def setenv(self, key, value):
        self._env.append((key, os.environ.get(key)))
        os.environ[key] = value

    def setattr(self, target, name, value):
        self._attrs.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for target, name, previous in reversed(self._attrs):
            setattr(target, name, previous)
        for key, previous in reversed(self._env):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def load_module(path):
    module_name = f"desktop_tests_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_kwargs(parameters):
    kwargs = {}
    cleanups = []
    for name in parameters:
        if name == "tmp_path":
            temp_dir = tempfile.TemporaryDirectory()
            cleanups.append(temp_dir.cleanup)
            kwargs[name] = Path(temp_dir.name)
        elif name == "monkeypatch":
            monkeypatch = MonkeyPatch()
            cleanups.append(monkeypatch.undo)
            kwargs[name] = monkeypatch
        else:
            raise RuntimeError(f"Unsupported fixture '{name}'")
    return kwargs, cleanups


def main():
    failures = []
    skipped = []
    total = 0

    for path in sorted(TEST_ROOT.glob("test_*.py")):
        try:
            module = load_module(path)
        except ModuleNotFoundError as exc:
            if exc.name == "pytest":
                skipped.append((path.name, "requires pytest"))
                print(f"SKIP {path.name}: requires pytest")
                continue
            raise

        for name, func in sorted(vars(module).items()):
            if not name.startswith("test_") or not callable(func):
                continue
            total += 1
            cleanups = []
            try:
                signature = inspect.signature(func)
                try:
                    kwargs, cleanups = build_kwargs(signature.parameters)
                except RuntimeError as exc:
                    skipped.append((f"{path.name}::{name}", str(exc)))
                    print(f"SKIP {path.name}::{name}: {exc}")
                    continue
                func(**kwargs)
                print(f"PASS {path.name}::{name}")
            except Exception as exc:
                failures.append((path.name, name, exc))
                print(f"FAIL {path.name}::{name}: {exc}")
            finally:
                for cleanup in reversed(cleanups):
                    cleanup()

    if failures:
        print(f"\n{len(failures)} failed, {total - len(failures)} passed, {len(skipped)} skipped, {total} total")
        return 1

    print(f"\n{total} passed, {len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
