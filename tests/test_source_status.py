from utils.source_status import SourceStatus, SourceStatusManager


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
