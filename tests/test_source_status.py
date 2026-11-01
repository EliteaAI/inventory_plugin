from utils.source_status import (
    SourceStatus,
    SourceStatusManager,
    should_clear_tracked_ingestion,
)


def test_stop_ingestion_marks_source_as_stopped(tmp_path):
    manager = SourceStatusManager(str(tmp_path))
    manager.start_ingestion(toolkit_id="7", toolkit_name="repo-seven", toolkit_type="github")

    manager.stop_ingestion(toolkit_id="7")

    source = manager.get_source_status("7")
    assert source["status"] == SourceStatus.STOPPED
    assert source["error_message"] is None
    assert source["progress_message"] == "Ingestion stopped by user"


def test_stop_ingestion_preserves_existing_counts(tmp_path):
    manager = SourceStatusManager(str(tmp_path))
    manager.complete_ingestion(toolkit_id="7", entities_count=11, relations_count=13, documents_processed=5)
    manager.start_ingestion(toolkit_id="7", toolkit_name="repo-seven", toolkit_type="github")

    manager.stop_ingestion(toolkit_id="7", documents_processed=2)

    source = manager.get_source_status("7")
    assert source["status"] == SourceStatus.STOPPED
    assert source["entities_count"] == 11
    assert source["relations_count"] == 13
    assert source["documents_processed"] == 2


def test_status_summary_counts_stopped_sources(tmp_path):
    manager = SourceStatusManager(str(tmp_path))
    manager.start_ingestion(toolkit_id="7", toolkit_name="repo-seven", toolkit_type="github")
    manager.stop_ingestion(toolkit_id="7")

    summary = manager.get_status_summary()

    assert summary["status_counts"][SourceStatus.STOPPED] == 1


def test_should_clear_tracked_ingestion_for_terminal_status_written_after_start():
    tracked_ingestion = {
        "task_id": "task-1",
        "toolkit_id": 7,
        "started_at": "2026-07-07T09:00:00+00:00",
    }
    source_info = {
        "status": SourceStatus.STOPPED,
        "started_at": "2026-07-07T09:00:01+00:00",
        "last_updated": "2026-07-07T09:04:13+00:00",
    }

    assert should_clear_tracked_ingestion(source_info, tracked_ingestion) is True


def test_should_not_clear_tracked_ingestion_for_new_run_before_status_refresh():
    tracked_ingestion = {
        "task_id": "task-2",
        "toolkit_id": 7,
        "started_at": "2026-07-07T10:00:00+00:00",
    }
    source_info = {
        "status": SourceStatus.COMPLETED,
        "started_at": "2026-07-07T09:30:00+00:00",
        "last_updated": "2026-07-07T09:59:00+00:00",
    }

    assert should_clear_tracked_ingestion(source_info, tracked_ingestion) is False
