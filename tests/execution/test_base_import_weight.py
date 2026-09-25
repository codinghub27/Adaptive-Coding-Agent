"""`app.execution.base` must stay import-light.

It's imported by `app.execution.runner` (and its tests) so the language-
agnostic dispatcher and its `SandboxBackend`/`RawRun` contracts don't have to
pay for importing `docker` -- only `app.execution.sandbox` (the one place
that talks to the Docker Engine API) needs that. Checked in a subprocess (of
our own fixed command, no user input) so a heavy import a *previous* test
already performed in this process can't mask a regression.
"""

import subprocess
import sys


def test_importing_execution_base_does_not_import_docker() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.execution.base, sys; print('docker' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stderr
