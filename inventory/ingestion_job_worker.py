#!/usr/bin/env python3
# coding=utf-8

"""Entry point for stateless K8s Job-based inventory ingestion."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

TERMINATION_LOG_PATH = "/dev/termination-log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ingestion_job_worker")


def write_termination_message(message: str, max_length: int = 4096) -> None:
    try:
        if len(message) > max_length:
            message = message[: max_length - 20] + "\n... (truncated)"
        with open(TERMINATION_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(message)
    except Exception:
        pass


def load_input(job_id: str) -> Optional[Dict[str, Any]]:
    base_path = Path(os.environ.get("INVENTORY_BASE_PATH", "/data/inventory"))
    input_file = base_path / "jobs" / job_id / "input.json"
    if input_file.exists():
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            log.error("Failed to load local input: %s", exc)

    try:
        from inventory.k8s_ingestion_job_manager import create_elitea_client_from_env, get_job_artifact_bucket, job_input_key

        client = create_elitea_client_from_env()
        if not client:
            return None
        data = client.artifact(get_job_artifact_bucket()).get(job_input_key(job_id))
        if _is_artifact_error(data):
            log.error("Job input artifact returned an error: %s", data)
            return None
        input_data = json.loads(data)
        input_file.parent.mkdir(parents=True, exist_ok=True)
        with open(input_file, "w", encoding="utf-8") as f:
            json.dump(input_data, f, indent=2)
        return input_data
    except Exception as exc:
        log.error("Failed to download job input from platform artifacts: %s", exc)
        return None


def save_result(job_id: str, result: Dict[str, Any]) -> None:
    base_path = Path(os.environ.get("INVENTORY_BASE_PATH", "/data/inventory"))
    result_file = base_path / "jobs" / job_id / "result.json"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    log.info("Saved local result to %s", result_file)

    try:
        from inventory.k8s_ingestion_job_manager import create_elitea_client_from_env, get_job_artifact_bucket, job_result_key

        client = create_elitea_client_from_env()
        if client:
            response = client.artifact(get_job_artifact_bucket()).create(job_result_key(job_id), json.dumps(result, indent=2, default=str))
            if isinstance(response, dict) and response.get("error"):
                raise RuntimeError(response["error"])
            log.info("Uploaded result.json to platform artifacts")
    except Exception as exc:
        log.warning("Failed to upload job result to platform artifacts: %s", exc)


def save_progress(job_id: str, elitea_client, artifact_bucket: str, progress: Dict[str, Any]) -> None:
    try:
        from inventory.k8s_ingestion_job_manager import job_progress_key

        response = elitea_client.artifact(artifact_bucket).create(job_progress_key(job_id), json.dumps(progress, indent=2, default=str))
        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(response["error"])
    except Exception as exc:
        log.debug("Failed to upload job progress to platform artifacts: %s", exc)


def categorize_error(error: Exception) -> str:
    error_str = str(error).lower()
    if "memory" in error_str or "oom" in error_str or "killed" in error_str:
        return "out_of_memory"
    if "timeout" in error_str or "timed out" in error_str:
        return "timeout"
    if "rate limit" in error_str or "429" in error_str:
        return "rate_limit"
    if "authentication" in error_str or "401" in error_str or "403" in error_str:
        return "authentication_error"
    if "toolkit" in error_str and ("load" in error_str or "instantiat" in error_str):
        return "toolkit_error"
    if "llm" in error_str or "model" in error_str:
        return "llm_error"
    return "pipeline_error"


def _is_artifact_error(data: str) -> bool:
    if not data:
        return True
    return data.startswith("Error:") or data.startswith('{"error"') or '"error"' in data[:100]


def _download_existing_artifacts(elitea_client, artifact_bucket: str, graph_dir: str, graph_path: str) -> None:
    """Warm the worker emptyDir from previously uploaded graph artifacts."""
    Path(graph_dir).mkdir(parents=True, exist_ok=True)
    try:
        graph_data = elitea_client.artifact(artifact_bucket).get("graph.json")
        if graph_data and not _is_artifact_error(graph_data):
            with open(graph_path, "w", encoding="utf-8") as f:
                f.write(graph_data)
            log.info("Downloaded existing graph.json from artifact bucket")
    except Exception as exc:
        log.info("No existing graph.json available: %s", exc)

    for artifact_name in ("sources_status.json",):
        try:
            data = elitea_client.artifact(artifact_bucket).get(artifact_name)
            if data and not _is_artifact_error(data):
                with open(os.path.join(graph_dir, artifact_name), "w", encoding="utf-8") as f:
                    f.write(data)
                log.info("Downloaded existing %s", artifact_name)
        except Exception:
            pass

    try:
        artifacts = elitea_client.artifact(artifact_bucket).list(return_as_string=False)
        for artifact_info in artifacts or []:
            if isinstance(artifact_info, dict):
                name = artifact_info.get("name", "")
                if name.startswith(".ingestion-checkpoint-"):
                    data = elitea_client.artifact(artifact_bucket).get(name)
                    if data and not _is_artifact_error(data):
                        with open(os.path.join(graph_dir, os.path.basename(name)), "w", encoding="utf-8") as f:
                            f.write(data)
                        log.info("Downloaded existing checkpoint %s", name)
    except Exception as exc:
        log.debug("No checkpoint artifacts available: %s", exc)


def _upload_output_artifacts(elitea_client, artifact_bucket: str, graph_dir: str, graph_path: str, toolkit_name: str, success: bool) -> Dict[str, bool]:
    uploaded = {"graph_uploaded": False, "checkpoint_uploaded": False, "sources_status_uploaded": False}
    if success and os.path.exists(graph_path):
        with open(graph_path, "rb") as f:
            elitea_client.artifact(artifact_bucket).create("graph.json", f.read())
        uploaded["graph_uploaded"] = True
        log.info("Uploaded graph.json to artifact bucket %s", artifact_bucket)

    checkpoint_name = f".ingestion-checkpoint-{toolkit_name}.json"
    checkpoint_file = os.path.join(graph_dir, checkpoint_name)
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "rb") as f:
            elitea_client.artifact(artifact_bucket).create(checkpoint_name, f.read())
        uploaded["checkpoint_uploaded"] = True
        log.info("Uploaded checkpoint %s", checkpoint_name)

    status_file = os.path.join(graph_dir, "sources_status.json")
    if os.path.exists(status_file):
        with open(status_file, "rb") as f:
            elitea_client.artifact(artifact_bucket).create("sources_status.json", f.read())
        uploaded["sources_status_uploaded"] = True
        log.info("Uploaded sources_status.json")

    return uploaded


def run_ingestion(job_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    from elitea_sdk.runtime.clients.client import EliteAClient
    from elitea_sdk.tools import instantiate_toolkit
    from inventory.ingestion import IngestionPipeline
    from utils.source_status import SourceStatusManager
    import requests as http_requests

    start_time = time.time()
    project_id = input_data.get("project_id")
    application_id = input_data.get("application_id")
    toolkit_id = input_data.get("toolkit_id")
    branch = input_data.get("branch")
    graph_path = input_data.get("graph_path")
    graph_dir = input_data.get("graph_dir") or (os.path.dirname(graph_path) if graph_path else None)
    full_rebuild = bool(input_data.get("full_rebuild"))
    platform_url = input_data.get("platform_url") or os.environ.get("PLATFORM_API_URL", "")
    platform_token = os.environ.get("AI_RUN_PLATFORM_TOKEN", "") or input_data.get("platform_token", "")
    inventory_settings = input_data.get("inventory_settings") or {}
    ingestion_config = input_data.get("ingestion_config") or {}
    artifact_bucket = input_data.get("artifact_bucket") or inventory_settings.get("toolkit_configuration_bucket") or "graphs"

    if not graph_path or not graph_dir:
        raise ValueError("graph_path is required")
    if not project_id or not application_id:
        raise ValueError("project_id and application_id are required")
    if not toolkit_id:
        raise ValueError("toolkit_id is required")
    if not platform_url or not platform_token:
        raise ValueError("platform URL/token are required")

    elitea_client = EliteAClient(base_url=platform_url.rstrip("/"), project_id=int(project_id), auth_token=platform_token)

    if not inventory_settings or not inventory_settings.get("toolkit_configuration_llm_model"):
        inventory_url = f"{platform_url.rstrip('/')}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{application_id}"
        response = http_requests.get(inventory_url, headers=elitea_client.headers, verify=False)
        if response.ok:
            inventory_settings = response.json().get("settings", {})
            artifact_bucket = inventory_settings.get("toolkit_configuration_bucket") or artifact_bucket

    llm_model = (
        inventory_settings.get("toolkit_configuration_llm_model")
        or inventory_settings.get("llm_model")
        or input_data.get("llm_model")
    )
    if not llm_model:
        raise ValueError("No LLM model configured for inventory ingestion")

    toolkit_url = f"{platform_url.rstrip('/')}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{toolkit_id}?expand=true"
    response = http_requests.get(toolkit_url, headers=elitea_client.headers, verify=False)
    if not response.ok:
        raise RuntimeError(f"Failed to fetch source toolkit {toolkit_id}: {response.status_code} - {response.text}")
    toolkit_data = response.json()
    toolkit_data.setdefault("settings", {})["elitea"] = elitea_client
    source_toolkit_instance = instantiate_toolkit(toolkit_data)
    if hasattr(source_toolkit_instance, "tools") and source_toolkit_instance.tools:
        source_toolkit = source_toolkit_instance.tools[0].api_wrapper
    elif hasattr(source_toolkit_instance, "api_wrapper"):
        source_toolkit = source_toolkit_instance.api_wrapper
    else:
        source_toolkit = source_toolkit_instance

    toolkit_name = toolkit_data.get("name") or input_data.get("toolkit_name") or f"toolkit_{toolkit_id}"
    toolkit_type = (
        toolkit_data.get("type")
        or getattr(source_toolkit, "toolkit_type", None)
        or type(source_toolkit).__name__.lower().replace("apiwrapper", "").replace("elitea", "")
    )

    os.makedirs(graph_dir, exist_ok=True)
    if not full_rebuild:
        _download_existing_artifacts(elitea_client, artifact_bucket, graph_dir, graph_path)
    elif os.path.exists(graph_path):
        os.remove(graph_path)

    status_manager = SourceStatusManager(graph_dir)
    status_manager.start_ingestion(toolkit_id=str(toolkit_id), toolkit_name=toolkit_name, toolkit_type=toolkit_type, branch=branch)
    progress_state = {"sequence": 0, "last_upload": 0.0}

    def publish_progress(message: str, phase: str, force: bool = False) -> None:
        now = time.time()
        if not force and now - progress_state["last_upload"] < 2.0:
            return
        progress_state["sequence"] += 1
        progress_state["last_upload"] = now
        save_progress(job_id, elitea_client, artifact_bucket, {
            "sequence": progress_state["sequence"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "message": message,
            "toolkit_id": str(toolkit_id),
            "toolkit_name": toolkit_name,
            "toolkit_type": toolkit_type,
        })

    publish_progress("Starting ingestion...", "start", force=True)

    source_configs = inventory_settings.get("toolkit_configuration_source_configs") or inventory_settings.get("source_configs") or {}
    source_config = source_configs.get(str(toolkit_id), {}) if isinstance(source_configs, dict) else {}
    file_patterns = source_config.get("file_patterns") or input_data.get("file_patterns") or ""
    exclude_patterns = source_config.get("exclude_patterns") or input_data.get("exclude_patterns") or ""
    if source_config.get("branch") and not branch:
        branch = source_config.get("branch")
    whitelist = [p.strip() for p in file_patterns.split(",") if p.strip()] if file_patterns else None
    blacklist = [p.strip() for p in exclude_patterns.split(",") if p.strip()] if exclude_patterns else None

    llm = elitea_client.get_llm(model_name=llm_model or "gpt-4o-mini", model_config={"temperature": 0.0, "max_tokens": 4096})

    def progress_callback(message: str, phase: str) -> None:
        log.info("[%s] %s", phase, message)
        status_manager.update_progress(toolkit_id=str(toolkit_id), progress_message=message)
        publish_progress(message, phase, force=phase in {"complete", "error"})

    pipeline = IngestionPipeline(
        llm=llm,
        elitea=elitea_client,
        graph_path=graph_path,
        auto_generate_embeddings=ingestion_config.get("generate_embeddings", True),
        progress_callback=progress_callback,
        max_parallel_extractions=ingestion_config.get("max_parallel_extractions", 10),
        batch_size=ingestion_config.get("batch_size", 10),
        max_parallel_chunks=ingestion_config.get("max_parallel_chunks", 5),
        min_file_lines=ingestion_config.get("min_file_lines", 20),
        min_file_chars=ingestion_config.get("min_file_chars", 300),
    )

    if hasattr(source_toolkit, "set_runnable_config"):
        source_toolkit.set_runnable_config({"run_id": uuid.uuid4(), "tags": ["inventory_plugin", "inventory", "ingest", "k8s_job"]})
    pipeline.register_toolkit(toolkit_name, source_toolkit)
    result = pipeline.run(source=toolkit_name, branch=branch, whitelist=whitelist, blacklist=blacklist, extract_relations=True)

    if result.success:
        status_manager.complete_ingestion(
            toolkit_id=str(toolkit_id),
            entities_count=result.entities_added,
            relations_count=result.relations_added,
            documents_processed=result.documents_processed,
        )
        publish_progress(
            f"Ingestion complete! {result.entities_added} entities, {result.relations_added} relations",
            "complete",
            force=True,
        )
    else:
        error_summary = result.errors[0] if result.errors else "Unknown error"
        status_manager.fail_ingestion(toolkit_id=str(toolkit_id), error_message=error_summary, documents_processed=result.documents_processed)
        publish_progress(error_summary, "error", force=True)

    uploaded = _upload_output_artifacts(elitea_client, artifact_bucket, graph_dir, graph_path, toolkit_name, result.success)
    duration = time.time() - start_time
    return {
        "success": result.success,
        "source": result.source,
        "toolkit_name": toolkit_name,
        "toolkit_type": toolkit_type,
        "artifact_bucket": artifact_bucket,
        "documents_processed": result.documents_processed,
        "entities_added": result.entities_added,
        "relations_added": result.relations_added,
        "errors": result.errors[:20] if result.errors else [],
        "duration_seconds": duration,
        "graph_path": graph_path,
        **uploaded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory ingestion K8s Job worker")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    job_id = args.job_id
    log.info("Inventory ingestion worker starting: job_id=%s", job_id)
    try:
        input_data = load_input(job_id)
        if input_data is None:
            raise ValueError("Failed to load job input")
        result = run_ingestion(job_id, input_data)
        save_result(job_id, result)
        if not result.get("success", False):
            error_msg = result.get("errors", ["Ingestion failed"])[0] if result.get("errors") else "Ingestion failed"
            write_termination_message(f"Inventory Ingestion Error\nError: {error_msg}\nCategory: pipeline_error\nJob ID: {job_id}")
            sys.exit(1)
        sys.exit(0)
    except Exception as exc:
        traceback_text = traceback.format_exc()
        error_category = categorize_error(exc)
        log.error("Inventory ingestion worker failed: %s\n%s", exc, traceback_text)
        write_termination_message(f"Inventory Ingestion Job Failed\nError: {exc}\nCategory: {error_category}\nJob ID: {job_id}")
        try:
            save_result(job_id, {"success": False, "error": str(exc), "error_category": error_category, "traceback": traceback_text})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()