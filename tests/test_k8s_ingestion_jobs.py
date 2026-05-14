from types import SimpleNamespace

from inventory.k8s_ingestion_job_manager import (
    DEFAULT_ARTIFACT_BUCKET,
    JOB_ARTIFACT_BUCKET_ENV,
    PLATFORM_API_URL_ENV,
    K8sIngestionJobManager,
    get_job_artifact_bucket,
    get_platform_api_url,
    job_input_key,
    job_result_key,
)


def test_job_artifact_keys_use_dedicated_prefix():
    assert job_input_key("abc") == "_inventory_jobs/abc/input.json"
    assert job_result_key("abc") == "_inventory_jobs/abc/result.json"


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
    }

    sanitized = K8sIngestionJobManager._sanitize_job_input(input_data)

    assert sanitized["project_id"] == 1
    assert sanitized["platform_url"] == "https://elitea.example.com"
    assert "platform_token" not in sanitized
    assert "artifact_x_secret" not in sanitized


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
