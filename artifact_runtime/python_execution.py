"""General Python execution inside the disposable, already isolated container.

This module is not a host executor. DockerBroker supplies the OS resource and
filesystem boundaries; only declared regular files leave that boundary.
"""

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from artifact_runtime.protocol import (
    GENERATED_TYPES,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    BlobDescriptor,
    GeneratedArtifact,
    PythonExecutionReport,
)

LOG_LIMIT = 65536
PYTHON_TIMEOUT = 80
THREAD_ENV = {
    key: "1"
    for key in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    )
}


def declared_files(root, declarations):
    files, total = [], 0
    for index, declaration in enumerate(declarations):
        path = root / declaration.path
        for part in [path, *path.parents]:
            if part == root:
                break
            if part.is_symlink():
                raise ValueError(f"Symlink output is not allowed: {declaration.path}")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise ValueError(f"Invalid output or file exceeds 20 MiB: {declaration.path}")
        total += info.st_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Outputs exceed 40 MiB")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        files.append(
            (
                BlobDescriptor(
                    name=f"output-{index}{path.suffix}",
                    mime_type=GENERATED_TYPES[path.suffix],
                    size_bytes=info.st_size,
                    sha256=digest.hexdigest(),
                ),
                path,
            )
        )
    return tuple(files)


def execute_python(request, *, input_dir: Path, output_dir: Path):
    arguments = request.operation.python
    if arguments is None:
        raise ValueError("A unified Python execution contract is required")
    env = {
        "PATH": os.defpath,
        "HOME": tempfile.gettempdir(),
        "LANG": "C.UTF-8",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": tempfile.gettempdir() + "/matplotlib",
        "XDG_CACHE_HOME": tempfile.gettempdir() + "/cache",
        "PYTHONIOENCODING": "utf-8",
        "AGENT_INPUT_DIR": str(input_dir),
        "AGENT_OUTPUT_DIR": str(output_dir),
        **THREAD_ENV,
    }
    # Windows local unit tests require SystemRoot; production Linux has no such secret.
    if os.name == "nt":
        env["SystemRoot"] = os.environ["SystemRoot"]
    status, code, files = "success", 0, ()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.run(
                [sys.executable, "-I", "-B", "-c", arguments.code],
                cwd=output_dir,
                input=(arguments.stdin or "").encode(),
                stdout=stdout,
                stderr=stderr,
                timeout=PYTHON_TIMEOUT,
                env=env,
                check=False,
            )
            code = process.returncode
            if code:
                status = "error"
            else:
                files = declared_files(output_dir, arguments.outputs)
        except subprocess.TimeoutExpired:
            status, code = "timeout", None
            stderr.write(b"\nPython execution exceeded the per-call time limit.")
        except (ValueError, OSError) as error:
            status, code = "error", None
            stderr.write(("\n" + str(error)).encode("utf-8", "replace"))
        sizes = stdout.tell(), stderr.tell()
        stdout.seek(0)
        stderr.seek(0)
        report = PythonExecutionReport(
            status=status,
            exit_code=code,
            stdout=stdout.read(LOG_LIMIT).decode("utf-8", "replace"),
            stderr=stderr.read(LOG_LIMIT).decode("utf-8", "replace"),
            logs_truncated=any(size > LOG_LIMIT for size in sizes),
            primary_output=(arguments.primary_output or arguments.outputs[0].path)
            if files
            else None,
        )
    return GeneratedArtifact(files=tuple(item[0] for item in files), execution=report), files
