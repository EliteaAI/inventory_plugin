#!/usr/bin/env python3
# coding=utf-8

"""Kubernetes Job manager for stateless inventory ingestion workers."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

WORKER_APP_LABEL = "inventory-worker"
JOB_NAME_PREFIX = "inventory-worker-"
DEFAULT_ARTIFACT_BUCKET = "graphs"
JOB_OBJECT_PREFIX = "_inventory_jobs"
JOB_ARTIFACT_BUCKET_ENV = "INVENTORY_ARTIFACT_BUCKET"
JOB_PROJECT_ID_ENV = "INVENTORY_PROJECT_ID"
PLATFORM_API_URL_ENV = "PLATFORM_API_URL"
SENSITIVE_INPUT_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "client_secret",
    "refresh_token",
)


def job_input_key(job_id: str) -> str:
    return f"{JOB_OBJECT_PREFIX}/{job_id}/input.json"


def job_result_key(job_id: str) -> str:
    return f"{JOB_OBJECT_PREFIX}/{job_id}/result.json"


def job_progress_key(job_id: str) -> str:
    return f"{JOB_OBJECT_PREFIX}/{job_id}/progress.json"


def get_job_artifact_bucket(input_data: Optional[Dict[str, Any]] = None) -> str:
    if input_data:
        return input_data.get("artifact_bucket") or DEFAULT_ARTIFACT_BUCKET
    return os.environ.get(JOB_ARTIFACT_BUCKET_ENV, DEFAULT_ARTIFACT_BUCKET).strip() or DEFAULT_ARTIFACT_BUCKET


def get_platform_api_url(input_data: Optional[Dict[str, Any]] = None) -> str:
    if input_data:
        return (input_data.get("platform_url") or "").rstrip("/")
    return os.environ.get(PLATFORM_API_URL_ENV, "").strip().rstrip("/")


def create_elitea_client(base_url: str, auth_token: str, project_id: Any):
    from elitea_sdk.runtime.clients.client import EliteAClient

    if not base_url:
        raise ValueError("platform URL is required")
    if not auth_token:
        raise ValueError("platform token is required")
    if not project_id:
        raise ValueError("project_id is required")
    return EliteAClient(base_url=base_url.rstrip("/"), project_id=int(project_id), auth_token=auth_token)


def create_elitea_client_from_env():
    return create_elitea_client(
        get_platform_api_url(),
        os.environ.get("AI_RUN_PLATFORM_TOKEN", ""),
        os.environ.get(JOB_PROJECT_ID_ENV, ""),
    )


def is_artifact_error(data: Any) -> bool:
    if not data:
        return True
    if isinstance(data, dict) and data.get("error"):
        return True
    if isinstance(data, str):
        return data.startswith("Error:") or data.startswith('{"error"') or '"error"' in data[:100]
    return False


def is_jobs_enabled() -> bool:
    """Return True when Inventory ingestion should run in K8s Jobs."""
    return os.environ.get("INVENTORY_JOBS_ENABLED", "false").lower() == "true"


class K8sIngestionJobManager:
    """Create, monitor, and clean up Inventory ingestion Jobs."""

    def __init__(self, base_path: str | None = None):
        self.namespace = os.environ.get("INVENTORY_NAMESPACE", "inventory")
        self.max_concurrent_jobs = int(os.environ.get("INVENTORY_MAX_CONCURRENT_JOBS", "3"))
        self.base_path = base_path or os.environ.get("INVENTORY_BASE_PATH", "/data/inventory")
        self.jobs_dir = Path(self.base_path) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

        self.worker_image = os.environ.get("INVENTORY_WORKER_IMAGE", os.environ.get("INVENTORY_IMAGE", "pylon:latest"))
        self.image_pull_policy = os.environ.get("INVENTORY_WORKER_IMAGE_PULL_POLICY", "IfNotPresent")
        self.ttl_seconds = int(os.environ.get("INVENTORY_JOB_TTL_SECONDS", "300"))
        self.resources = {
            "requests": {
                "memory": os.environ.get("INVENTORY_JOB_MEMORY_REQUEST", "2Gi"),
                "cpu": os.environ.get("INVENTORY_JOB_CPU_REQUEST", "1"),
            },
            "limits": {
                "memory": os.environ.get("INVENTORY_JOB_MEMORY_LIMIT", "8Gi"),
                "cpu": os.environ.get("INVENTORY_JOB_CPU_LIMIT", "4"),
            },
        }

        self._k8s_client = None

    def _init_k8s_client(self) -> None:
        if self._k8s_client is not None:
            return
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
                log.info("Loaded in-cluster K8s config")
            except config.ConfigException:
                config.load_kube_config()
                log.info("Loaded local kubeconfig")
            self._k8s_client = client
        except ImportError as exc:
            raise RuntimeError("kubernetes package not installed") from exc

    def _get_batch_api(self):
        self._init_k8s_client()
        return self._k8s_client.BatchV1Api()

    def _get_core_api(self):
        self._init_k8s_client()
        return self._k8s_client.CoreV1Api()

    @staticmethod
    def generate_job_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{timestamp}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _sanitize_job_input(input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove secrets from input.json before uploading it to artifacts."""
        def is_sensitive_key(key: Any) -> bool:
            normalized = str(key).lower().replace("-", "_")
            return any(part in normalized for part in SENSITIVE_INPUT_KEY_PARTS)

        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: sanitize(item) for key, item in value.items() if not is_sensitive_key(key)}
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            return value

        return sanitize(input_data)

    def _get_elitea_client(self, input_data: Dict[str, Any]):
        return create_elitea_client(
            get_platform_api_url(input_data),
            input_data.get("platform_token") or os.environ.get("AI_RUN_PLATFORM_TOKEN", ""),
            input_data.get("project_id") or "",
        )

    def _upload_job_input(self, job_id: str, input_data: Dict[str, Any], elitea_client) -> bool:
        if not elitea_client:
            return False
        try:
            bucket = get_job_artifact_bucket(input_data)
            payload = json.dumps(self._sanitize_job_input(input_data), indent=2, default=str)
            result = elitea_client.artifact(bucket).create(job_input_key(job_id), payload)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result["error"])
            log.info("Uploaded job input to artifact bucket: %s/%s", bucket, job_input_key(job_id))
            return True
        except Exception as exc:
            log.error("Failed to upload Inventory job input: %s", exc)
            return False

    def _download_job_result(self, job_id: str, input_data: Dict[str, Any], elitea_client=None) -> Optional[Dict[str, Any]]:
        client = elitea_client or self._get_elitea_client(input_data)
        if not client:
            return None
        try:
            bucket = get_job_artifact_bucket(input_data)
            data = client.artifact(bucket).get(job_result_key(job_id))
            if is_artifact_error(data):
                raise RuntimeError(str(data))
            result = json.loads(data)
            result_file = self.jobs_dir / job_id / "result.json"
            result_file.parent.mkdir(parents=True, exist_ok=True)
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, default=str)
            return result
        except Exception as exc:
            log.warning("Failed to download Inventory job result for %s: %s", job_id, exc)
            return None

    def _cleanup_platform_job_objects(self, job_id: str, input_data: Dict[str, Any]) -> None:
        client = self._get_elitea_client(input_data)
        if not client:
            return
        bucket = get_job_artifact_bucket(input_data)
        artifact = client.artifact(bucket)
        for key in (job_input_key(job_id), job_result_key(job_id), job_progress_key(job_id)):
            try:
                artifact.delete(key, check_exists=False)
            except TypeError:
                try:
                    artifact.delete(key)
                except Exception:
                    pass
            except Exception:
                pass

    def get_slot_availability(self) -> Dict[str, Any]:
        batch_v1 = self._get_batch_api()
        jobs = batch_v1.list_namespaced_job(namespace=self.namespace, label_selector=f"app={WORKER_APP_LABEL}")
        active_count = sum(1 for job in jobs.items if job.status.active and job.status.active > 0)
        available = max(0, self.max_concurrent_jobs - active_count)
        return {
            "available": available,
            "total": self.max_concurrent_jobs,
            "active": active_count,
            "can_start": active_count < self.max_concurrent_jobs,
            "mode": "jobs",
        }

    def create_job(self, job_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        from kubernetes import client

        slots = self.get_slot_availability()
        if not slots["can_start"]:
            return {
                "success": False,
                "error": f"[SERVICE_BUSY] All {slots['total']} ingestion slots are in use",
                "error_category": "service_busy",
                "active_workers": slots["active"],
                "max_workers": slots["total"],
            }

        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        with open(job_dir / "input.json", "w", encoding="utf-8") as f:
            json.dump(self._sanitize_job_input(input_data), f, indent=2, default=str)

        elitea_client = self._get_elitea_client(input_data)
        if not self._upload_job_input(job_id, input_data, elitea_client):
            return {
                "success": False,
                "error": "Failed to upload ingestion job input to artifact bucket",
                "error_category": "platform_upload_failed",
            }

        plugin_path = "/data/plugins/inventory_plugin"
        baked_plugin_path = "/app/inventory_plugin"
        requirements_path = "/data/requirements/inventory_plugin/lib/python3.12/site-packages"
        prebaked_path = "/opt/inventory/lib/python3.12/site-packages"
        worker_script = "inventory/ingestion_job_worker.py"

        env_vars = [
            client.V1EnvVar(name="INVENTORY_JOB_ID", value=job_id),
            client.V1EnvVar(name="INVENTORY_BASE_PATH", value="/data/inventory"),
            client.V1EnvVar(name="PYTHONUNBUFFERED", value="1"),
            client.V1EnvVar(
                name="PYTHONPATH",
                value=f"{prebaked_path}:{requirements_path}:{plugin_path}:{baked_plugin_path}:/data/plugins:/app",
            ),
            client.V1EnvVar(name=PLATFORM_API_URL_ENV, value=get_platform_api_url(input_data)),
            client.V1EnvVar(name="AI_RUN_PLATFORM_TOKEN", value=input_data.get("platform_token") or ""),
            client.V1EnvVar(name=JOB_PROJECT_ID_ENV, value=str(input_data.get("project_id") or "")),
            client.V1EnvVar(name=JOB_ARTIFACT_BUCKET_ENV, value=get_job_artifact_bucket(input_data)),
        ]

        already_set = {ev.name for ev in env_vars}
        for var_name, var_value in os.environ.items():
            if var_name.startswith("INVENTORY_") and var_name not in already_set:
                env_vars.append(client.V1EnvVar(name=var_name, value=var_value))
                already_set.add(var_name)

        job_name = f"{JOB_NAME_PREFIX}{job_id}"
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={"app": WORKER_APP_LABEL, "job-id": job_id, "created-by": "inventory-controller"},
            ),
            spec=client.V1JobSpec(
                ttl_seconds_after_finished=self.ttl_seconds,
                backoff_limit=0,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app": WORKER_APP_LABEL, "job-id": job_id}),
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        service_account_name=os.environ.get("INVENTORY_WORKER_SERVICE_ACCOUNT", "default"),
                        security_context=client.V1PodSecurityContext(run_as_user=33, run_as_group=33, fs_group=33),
                        containers=[
                            client.V1Container(
                                name="worker",
                                image=self.worker_image,
                                image_pull_policy=self.image_pull_policy,
                                command=["/bin/sh", "-c"],
                                args=[
                                    f'W={plugin_path}/{worker_script}; '
                                    f'[ -f "$W" ] || W={baked_plugin_path}/{worker_script}; '
                                    f'exec python "$W" --job-id={job_id}'
                                ],
                                env=env_vars,
                                security_context=client.V1SecurityContext(run_as_user=33, run_as_group=33),
                                volume_mounts=[client.V1VolumeMount(name="data", mount_path="/data")],
                                resources=client.V1ResourceRequirements(
                                    requests=self.resources["requests"],
                                    limits=self.resources["limits"],
                                ),
                            )
                        ],
                        volumes=[client.V1Volume(name="data", empty_dir=client.V1EmptyDirVolumeSource())],
                    ),
                ),
            ),
        )

        try:
            self._get_batch_api().create_namespaced_job(namespace=self.namespace, body=job)
            log.info("Created Inventory ingestion Job: %s", job_name)
            return {"success": True, "job_id": job_id, "job_name": job_name, "namespace": self.namespace}
        except Exception as exc:
            log.error("Failed to create Inventory ingestion Job: %s", exc)
            return {"success": False, "error": f"Failed to create K8s Job: {exc}"}

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        job_name = f"{JOB_NAME_PREFIX}{job_id}"
        try:
            job = self._get_batch_api().read_namespaced_job(name=job_name, namespace=self.namespace)
            if job.status.succeeded and job.status.succeeded > 0:
                phase = "succeeded"
            elif job.status.failed and job.status.failed > 0:
                phase = "failed"
            elif job.status.active and job.status.active > 0:
                phase = "running"
            else:
                phase = "pending"
            return {"success": True, "job_id": job_id, "job_name": job_name, "phase": phase}
        except Exception as exc:
            if "NotFound" in str(exc):
                return {"success": False, "job_id": job_id, "phase": "not_found", "error": str(exc)}
            return {"success": False, "job_id": job_id, "phase": "error", "error": str(exc)}

    def get_job_failure_info(self, job_id: str) -> Dict[str, Any]:
        result = {"error": "Unknown job failure", "error_category": "unknown_error", "exit_code": None}
        job_name = f"{JOB_NAME_PREFIX}{job_id}"
        try:
            pods = self._get_core_api().list_namespaced_pod(namespace=self.namespace, label_selector=f"job-name={job_name}")
            if pods.items and pods.items[0].status.container_statuses:
                for status in pods.items[0].status.container_statuses:
                    terminated = getattr(getattr(status, "state", None), "terminated", None)
                    if terminated:
                        result["exit_code"] = terminated.exit_code
                        if terminated.message:
                            result["error"] = terminated.message.split("\n")[0]
                        if terminated.reason == "OOMKilled":
                            result["error"] = "Job ran out of memory (OOMKilled)"
                            result["error_category"] = "out_of_memory"
        except Exception as exc:
            result["error"] = f"Failed to get job failure info: {exc}"
        return result

    def get_job_pod_name(self, job_id: str, timeout: int = 60) -> Optional[str]:
        job_name = f"{JOB_NAME_PREFIX}{job_id}"
        core_v1 = self._get_core_api()
        for _ in range(timeout):
            try:
                pods = core_v1.list_namespaced_pod(namespace=self.namespace, label_selector=f"job-name={job_name}")
                if pods.items:
                    return pods.items[0].metadata.name
            except Exception as exc:
                log.debug("Error listing Inventory job pods: %s", exc)
            time.sleep(1)
        return None

    @staticmethod
    def _is_worker_container_loggable(container_statuses: list[Any]) -> bool:
        worker_status = next((status for status in container_statuses if getattr(status, "name", "") == "worker"), None)
        if worker_status is None and container_statuses:
            worker_status = container_statuses[0]
        if worker_status is None:
            return False
        state = getattr(worker_status, "state", None)
        return bool(getattr(state, "running", None) or getattr(state, "terminated", None))

    @staticmethod
    def _is_log_startup_race(exc: Exception) -> bool:
        error_text = str(exc)
        return "waiting to start" in error_text or "ContainerCreating" in error_text or "PodInitializing" in error_text

    def _wait_for_pod_logs_ready(self, core_v1: Any, pod_name: str, timeout: int) -> bool:
        for _ in range(timeout):
            try:
                pod = core_v1.read_namespaced_pod(name=pod_name, namespace=self.namespace)
                pod_status = getattr(pod, "status", None)
                container_statuses = getattr(pod_status, "container_statuses", None) or []
                if self._is_worker_container_loggable(container_statuses):
                    return True
            except Exception as exc:
                log.debug("Error reading Inventory job pod status: %s", exc)
            time.sleep(1)
        return False

    def stream_job_logs(self, job_id: str, callback: Callable[[str], None], timeout: int = 60) -> None:
        self._init_k8s_client()
        core_v1 = self._k8s_client.CoreV1Api()
        pod_name = self.get_job_pod_name(job_id, timeout=timeout)
        if not pod_name:
            callback(f"[ERROR] Pod for job {job_id} was not created within {timeout}s")
            return
        try:
            startup_deadline = time.time() + timeout
            while time.time() < startup_deadline:
                if not self._wait_for_pod_logs_ready(core_v1, pod_name, timeout=1):
                    continue
                try:
                    # kubernetes client >=31 dropped the implicit ``watch`` kwarg that
                    # ``Watch().stream()`` injects into ``read_namespaced_pod_log``; stream
                    # the raw HTTP response directly instead (line-iterable, follow=True).
                    log_stream = core_v1.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                        follow=True,
                        _preload_content=False,
                    )
                    try:
                        for raw_line in log_stream:
                            callback(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
                    finally:
                        log_stream.close()
                    return
                except Exception as exc:
                    if self._is_log_startup_race(exc):
                        log.debug("Inventory job log stream not ready yet for pod %s: %s", pod_name, exc)
                        time.sleep(1)
                        continue
                    callback(f"[ERROR] Log streaming failed: {exc}")
                    return
            callback(f"[ERROR] Worker container logs for job {job_id} were not available within {timeout}s")
        except Exception as exc:
            callback(f"[ERROR] Log streaming failed: {exc}")

    def read_job_result(self, job_id: str, input_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        result_file = self.jobs_dir / job_id / "result.json"
        if result_file.exists():
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return self._download_job_result(job_id, input_data)

    def read_job_progress(self, job_id: str, input_data: Dict[str, Any], elitea_client=None) -> Optional[Dict[str, Any]]:
        client = elitea_client or self._get_elitea_client(input_data)
        if not client:
            return None
        try:
            bucket = get_job_artifact_bucket(input_data)
            data = client.artifact(bucket).get(job_progress_key(job_id))
            if is_artifact_error(data):
                return None
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8", errors="replace")
            if isinstance(data, dict):
                return data
            if isinstance(data, str):
                data = data.strip()
                if not data:
                    return None
                return json.loads(data)
            return None
        except Exception as exc:
            log.debug("Failed to read Inventory job progress: %s", exc)
            return None

    def cleanup_job(self, job_id: str, input_data: Dict[str, Any], delete_k8s_job: bool = False) -> None:
        import shutil

        job_dir = self.jobs_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        self._cleanup_platform_job_objects(job_id, input_data)
        if delete_k8s_job:
            try:
                self._get_batch_api().delete_namespaced_job(
                    name=f"{JOB_NAME_PREFIX}{job_id}",
                    namespace=self.namespace,
                    propagation_policy="Background",
                )
            except Exception as exc:
                if "NotFound" not in str(exc):
                    log.warning("Failed to delete Inventory ingestion Job: %s", exc)


_job_manager: Optional[K8sIngestionJobManager] = None


def get_ingestion_job_manager(base_path: str | None = None) -> K8sIngestionJobManager:
    global _job_manager
    if _job_manager is None:
        _job_manager = K8sIngestionJobManager(base_path=base_path)
    return _job_manager