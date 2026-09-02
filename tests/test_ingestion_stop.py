import threading
import time
import builtins

import pytest
from langchain_core.documents import Document

from inventory.ingestion import IngestionPipeline


class InterruptTaskThread(BaseException):
    """Mirror the task interruption type name used by the runtime."""


def test_iter_completed_futures_checks_stop_while_waiting(tmp_path):
    pipeline = IngestionPipeline(
        llm=None,
        graph_path=str(tmp_path / "graph.json"),
        source_toolkits={},
    )

    checks = 0
    release = threading.Event()

    def stop_callback():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise InterruptTaskThread()

    pipeline.stop_callback = stop_callback

    executor = None
    try:
        from concurrent.futures import ThreadPoolExecutor

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(release.wait, 1.0)

        started = time.monotonic()
        with pytest.raises(InterruptTaskThread):
            list(pipeline._iter_completed_futures([future], poll_interval=0.01))

        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        if executor is not None:
            executor.shutdown(wait=True)


def test_run_checks_stop_between_loader_documents(tmp_path, monkeypatch):
    pipeline = IngestionPipeline(
        llm=None,
        graph_path=str(tmp_path / "graph.json"),
        source_toolkits={},
    )

    yielded_paths = []
    normalized_paths = []

    class FakeToolkit:
        def loader(self, **kwargs):
            assert kwargs["chunked"] is False
            for index in range(1, 4):
                file_path = f"file_{index}.py"
                yielded_paths.append(file_path)
                yield Document(
                    page_content=f"print({index})",
                    metadata={"file_path": file_path},
                )

    def normalize_document(doc, source):
        normalized_paths.append(doc.metadata["file_path"])
        return doc

    def stop_callback():
        if len(yielded_paths) >= 2:
            raise InterruptTaskThread()

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (
            name.startswith("elitea_sdk.tools.chunkers")
            or name == "langchain.text_splitter"
        ):
            raise ImportError("chunkers disabled for stop test")
        return real_import(name, globals, locals, fromlist, level)

    pipeline.register_toolkit("fake", FakeToolkit())
    pipeline.stop_callback = stop_callback

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(pipeline, "_init_extractors", lambda: True)
    monkeypatch.setattr(pipeline, "_normalize_document", normalize_document)
    monkeypatch.setattr(pipeline, "_save_checkpoint", lambda checkpoint: None)
    monkeypatch.setattr(pipeline, "_auto_save", lambda: None)

    with pytest.raises(InterruptTaskThread):
        pipeline.run(source="fake", extract_relations=False, resume=False)

    assert yielded_paths == ["file_1.py", "file_2.py"]
    assert normalized_paths == ["file_1.py"]
