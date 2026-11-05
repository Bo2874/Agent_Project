def test_requested_document_formats_select_real_acceptance_tests():
    from scripts.check_artifact_workflow import workflow_tests

    old = workflow_tests({"xlsx"})
    assert "tests/test_artifact_workflow.py" in old
    assert "tests/test_pdf_artifacts.py" not in old
    documents = workflow_tests({"docx", "pdf"})
    for required in (
        "tests/test_word_tools.py",
        "tests/test_pdf_artifacts.py",
        "tests/test_document_browser.py",
        "tests/test_document_rendering.py",
        "tests/test_artifact_preview_assets.py",
        "tests/test_artifact_bundles.py",
    ):
        assert required in documents
