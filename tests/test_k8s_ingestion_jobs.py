from types import SimpleNamespace

from inventory.k8s_ingestion_job_manager import (
    DEFAULT_ARTIFACT_BUCKET,
    JOB_ARTIFACT_BUCKET_ENV,
    PLATFORM_API_URL_ENV,
    K8sIngestionJobManager,
    get_job_artifact_bucket,
    get_platform_api_url,
    job_input_key,
    job_progress_key,
    job_result_key,
)
from utils.ingestion_tracker import IngestionTracker


def test_job_artifact_keys_use_dedicated_prefix():
    assert job_input_key("abc") == "_inventory_jobs/abc/input.json"
    assert job_result_key("abc") == "_inventory_jobs/abc/result.json"
    assert job_progress_key("abc") == "_inventory_jobs/abc/progress.json"


def test_job_artifact_bucket_uses_payload_then_env(monkeypatch):
    assert get_job_artifact_bucket({"artifact_bucket": "payload-graphs"}) == "payload-graphs"

    monkeypatch.setenv(JOB_ARTIFACT_BUCKET_ENV, "env-graphs")
    assert get_job_artifact_bucket() == "env-graphs"

    monkeypatch.delenv(JOB_ARTIFACT_BUCKET_ENV)
    assert get_job_artifact_bucket() == DEFAULT_ARTIFACT_BUCKET


def test_platform_api_url_uses_payload_then_env(monkeypatch):
    assert get_platform_api_url({"platform_url": "https://elitea.example.com/"}) == "https://elitea.example.com"

    monkeypatch.setenv(PLATFORM_API_URL_ENV, "https://platform.example.com/")
    assert get_platform_api_url() == "https://platform.example.com"


def test_job_input_sanitization_removes_secrets():
    input_data = {
        "project_id": 1,
        "platform_url": "https://elitea.example.com",
        "platform_token": "secret-token",
        "artifact_x_secret": "secret-header",
        "custom_headers": {"Authorization": "Bearer secret", "X-Trace-Id": "trace-1"},
        "nested": [{"api_key": "secret-api-key", "safe": "value"}],
    }

    sanitized = K8sIngestionJobManager._sanitize_job_input(input_data)

    assert sanitized["project_id"] == 1
    assert sanitized["platform_url"] == "https://elitea.example.com"
    assert "platform_token" not in sanitized
    assert "artifact_x_secret" not in sanitized
    assert sanitized["custom_headers"] == {"X-Trace-Id": "trace-1"}
    assert sanitized["nested"] == [{"safe": "value"}]


def test_create_job_passes_platform_api_url_to_worker_env(monkeypatch, tmp_path):
    created = {}

    class FakeBatchApi:
        def create_namespaced_job(self, namespace, body):
            created["namespace"] = namespace
            created["body"] = body

    manager = K8sIngestionJobManager(base_path=str(tmp_path))
    monkeypatch.setattr(manager, "get_slot_availability", lambda: {"can_start": True})
    monkeypatch.setattr(manager, "_get_elitea_client", lambda input_data: object())
    monkeypatch.setattr(manager, "_upload_job_input", lambda job_id, input_data, elitea_client: True)
    monkeypatch.setattr(manager, "_get_batch_api", lambda: FakeBatchApi())

    result = manager.create_job(
        "job1",
        {
            "project_id": "7",
            "platform_url": "https://elitea.example.com/api",
            "platform_token": "token",
            "artifact_bucket": "graphs",
        },
    )

    assert result["success"] is True
    container = created["body"].spec.template.spec.containers[0]
    env = {item.name: item.value for item in container.env}
    assert env[PLATFORM_API_URL_ENV] == "https://elitea.example.com/api"
    assert "/app" in env["PYTHONPATH"].split(":")
    assert "AI_RUN_PLATFORM_URL" not in env


def test_worker_logs_wait_until_container_is_running(monkeypatch, tmp_path):
    class FakeCoreApi:
        def __init__(self):
            self.calls = 0

        def read_namespaced_pod(self, name, namespace):
            self.calls += 1
            if self.calls == 1:
                state = SimpleNamespace(waiting=SimpleNamespace(reason="ContainerCreating"))
            else:
                state = SimpleNamespace(running=SimpleNamespace(started_at="now"))
            status = SimpleNamespace(name="worker", state=state)
            return SimpleNamespace(status=SimpleNamespace(container_statuses=[status]))

    manager = K8sIngestionJobManager(base_path=str(tmp_path))
    core_api = FakeCoreApi()
    monkeypatch.setattr("inventory.k8s_ingestion_job_manager.time.sleep", lambda seconds: None)

    assert manager._wait_for_pod_logs_ready(core_api, "pod-name", timeout=2) is True
    assert core_api.calls == 2


def test_read_job_progress_downloads_latest_artifact(tmp_path):
    class FakeArtifact:
        def get(self, key):
            assert key == "_inventory_jobs/job1/progress.json"
            return '{"sequence": 3, "phase": "progress", "message": "Processed 10 files"}'

    class FakeClient:
        def artifact(self, bucket):
            assert bucket == "graphs"
            return FakeArtifact()

    manager = K8sIngestionJobManager(base_path=str(tmp_path))

    progress = manager.read_job_progress("job1", {"artifact_bucket": "graphs"}, elitea_client=FakeClient())

    assert progress == {"sequence": 3, "phase": "progress", "message": "Processed 10 files"}


def test_read_job_progress_returns_none_when_artifact_missing(tmp_path):
    # elitea_sdk's artifact .get() returns a human-readable string (not JSON, not an
    # "error" dict) when the progress object does not exist yet. read_job_progress must
    # treat it as "no progress yet" and not raise / log a JSON decode error.
    class FakeArtifact:
        def get(self, key):
            return "File '_inventory_jobs/job1/progress.json' not found. "

    class FakeClient:
        def artifact(self, bucket):
            return FakeArtifact()

    manager = K8sIngestionJobManager(base_path=str(tmp_path))

    progress = manager.read_job_progress("job1", {"artifact_bucket": "graphs"}, elitea_client=FakeClient())

    assert progress is None


def test_read_job_progress_ignores_non_object_json(tmp_path):
    # Progress is consumed via ``.get()``; a JSON array (or any non-object) must not be
    # returned as progress, otherwise call sites would crash on ``list.get``.
    class FakeArtifact:
        def get(self, key):
            return "[1, 2, 3]"

    class FakeClient:
        def artifact(self, bucket):
            return FakeArtifact()

    manager = K8sIngestionJobManager(base_path=str(tmp_path))

    progress = manager.read_job_progress("job1", {"artifact_bucket": "graphs"}, elitea_client=FakeClient())

    assert progress is None


def test_iter_log_lines_reassembles_split_chunks():
    # A single logical log line can arrive across multiple byte chunks; _iter_log_lines
    # must buffer and emit only whole, newline-delimited lines.
    class FakeStream:
        def stream(self, *args, **kwargs):
            yield b"[batch] Proce"
            yield b"ssing final batch 5 (9 files)\n[done] "
            yield b"finished\n"

    lines = list(K8sIngestionJobManager._iter_log_lines(FakeStream()))

    assert lines == ["[batch] Processing final batch 5 (9 files)", "[done] finished"]



def test_cleanup_platform_job_objects_removes_progress_artifact(monkeypatch, tmp_path):
    deleted_keys = []

    class FakeArtifact:
        def delete(self, key, check_exists=False):
            deleted_keys.append((key, check_exists))

    class FakeClient:
        def artifact(self, bucket):
            assert bucket == "graphs"
            return FakeArtifact()

    manager = K8sIngestionJobManager(base_path=str(tmp_path))
    monkeypatch.setattr(manager, "_get_elitea_client", lambda input_data: FakeClient())

    manager._cleanup_platform_job_objects("job1", {"artifact_bucket": "graphs"})

    assert deleted_keys == [
        (job_input_key("job1"), False),
        (job_result_key("job1"), False),
        (job_progress_key("job1"), False),
    ]


def test_cleanup_job_can_delete_k8s_job(monkeypatch, tmp_path):
    deleted_jobs = []

    class FakeBatchApi:
        def delete_namespaced_job(self, name, namespace, propagation_policy):
            deleted_jobs.append((name, namespace, propagation_policy))

    manager = K8sIngestionJobManager(base_path=str(tmp_path))
    monkeypatch.setattr(manager, "_cleanup_platform_job_objects", lambda job_id, input_data: None)
    monkeypatch.setattr(manager, "_get_batch_api", lambda: FakeBatchApi())

    manager.cleanup_job("job1", {"artifact_bucket": "graphs"}, delete_k8s_job=True)

    assert deleted_jobs == [("inventory-worker-job1", "inventory", "Background")]


def test_ingestion_tracker_updates_progress(tmp_path):
    tracker = IngestionTracker(base_path=str(tmp_path), max_parallel=2)
    tracker.acquire_slot(task_id="task1", project_id=2, toolkit_id=1, application_id=9)

    assert tracker.update_progress("task1", "Processed 10 files", progress_phase="progress") is True

    active = tracker.get_active_ingestions()
    assert active[0]["progress_message"] == "Processed 10 files"
    assert active[0]["progress_phase"] == "progress"
    assert active[0]["last_updated"]
