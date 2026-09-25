"""`app.graph.state` must stay import-light.

It's imported by (almost) every graph module, and (through `app.knowledge.base`)
only depends on `app.schemas.knowledge` + typing for its `Retriever` contract
-- never `qdrant_client` or `fastembed`, which are multi-second-to-import
model SDKs. Similarly, its `CodeRunner` contract (`app.execution.base`)
depends only on `app.schemas.execution` + typing -- never `docker`, a
heavyweight SDK `app.execution.runner`/`app.execution.sandbox` need but
`app.graph.state` must not pull in. This is checked in a subprocess (of our
own fixed command, no user input) so the heavy modules a *previous* test may
have already imported into this process can't mask a regression.
"""

import subprocess
import sys


def test_importing_graph_state_does_not_import_fastembed_or_onnxruntime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.graph.state, sys; "
            "print('fastembed' in sys.modules, 'onnxruntime' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False False", result.stderr


def test_importing_graph_state_does_not_import_docker() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import app.graph.state, sys; print('docker' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stderr
