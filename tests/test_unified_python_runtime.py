"""The general runtime returns observations even when it produces no files."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from artifact_runtime import protocol


def request(**kwargs):
    code = kwargs.pop("code")
    return protocol.ArtifactRunRequest(
        job_id=uuid4(),
        operation={"kind": "python", "code": code, "python": {"code": code, **kwargs}},
    )


def execute(tmp_path, **kwargs):
    from artifact_runtime.python_execution import execute_python

    inputs = tmp_path / "inputs"
    outputs = tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    return execute_python(request(**kwargs), input_dir=inputs, output_dir=outputs)


def test_calculation_without_files_returns_stdout(tmp_path):
    report, files = execute(tmp_path, code="print(sum(range(11)))")
    assert report.execution.status == "success"
    assert report.execution.stdout.splitlines() == ["55"]
    assert files == ()


def test_stdin_and_bounded_separate_logs(tmp_path):
    report, _ = execute(
        tmp_path,
        code="import sys; print(input()); print('x'*100000, file=sys.stderr)",
        stdin="hello\n",
    )
    assert report.execution.stdout.splitlines() == ["hello"]
    assert len(report.execution.stderr.encode()) <= 65536
    assert report.execution.logs_truncated


def test_python_error_is_observation_for_agent_to_correct(tmp_path):
    report, files = execute(tmp_path, code="raise ValueError('please correct me')")
    assert report.execution.status == "error"
    assert "ValueError: please correct me" in report.execution.stderr
    assert files == ()


def test_explicit_outputs_keep_declaration_order_and_ignore_scratch(tmp_path):
    report, files = execute(
        tmp_path,
        code=(
            "from pathlib import Path; Path('a.csv').write_text('a,b'); "
            "Path('b.json').write_text('{}'); Path('scratch.bin').write_bytes(b'abc')"
        ),
        outputs=[{"path": "b.json", "name": "report.json"}, {"path": "a.csv", "name": "data.csv"}],
        primary_output="a.csv",
    )
    assert [path.name for _, path in files] == ["b.json", "a.csv"]
    assert report.execution.primary_output == "a.csv"
    assert [item.name for item in report.files] == ["output-0.json", "output-1.csv"]


def test_multiple_outputs_require_explicit_primary():
    with pytest.raises(ValidationError, match="primary_output"):
        protocol.PythonCodeInput(code="pass", outputs=[{"path": "a.txt"}, {"path": "b.txt"}])


@pytest.mark.parametrize(
    "path", ["../oops.txt", "/oops.txt", "C:/oops.txt", "a\\oops.txt", "a/../oops.txt"]
)
def test_output_paths_cannot_escape(path):
    with pytest.raises(ValidationError):
        protocol.PythonOutput(path=path)


def test_declared_missing_output_is_an_error_observation(tmp_path):
    report, files = execute(tmp_path, code="print('done')", outputs=[{"path": "missing.xlsx"}])
    assert report.execution.status == "error"
    assert "missing.xlsx" in report.execution.stderr
    assert files == ()


def test_no_output_bundle_roundtrip(tmp_path):
    import io

    report, _ = execute(tmp_path, code="print(123)")
    stream = io.BytesIO()
    protocol.write_bundle(stream, report, ())
    stream.seek(0)
    loaded, paths = protocol.unpack_bundle(stream, tmp_path / "restored")
    assert loaded.execution.stdout.splitlines() == ["123"]
    assert paths == ()


def test_old_operation_wire_shape_stays_identical():
    value = {"kind": "python", "parent_version_id": None, "payload": {}, "code": "print(1)"}
    assert protocol.ArtifactOperation.model_validate(value).model_dump(mode="json") == value


def test_unified_broker_profile_has_one_image_and_consistent_limits():
    from artifact_runtime import containers

    assert containers.CODE_IMAGE == containers.RENDER_IMAGE == "personal-agent-python-runtime:1"
    for render in (False, True):
        limits = containers.DockerBroker.limits(render=render)
        assert "--memory=1024m" in limits
        assert "--ulimit=cpu=90:90" in limits
        assert "--cpus=1" in limits
        assert "--network=none" in limits
        assert "--user=65532:65532" in limits
