#!/usr/bin/env python3
"""Asynchronous A2A client with durable idempotency and coordinator-owned retries."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import time
from pathlib import Path

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import GetTaskRequest, Role, SendMessageConfiguration, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

TERMINAL = {
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED",
}


def dump_message(message) -> dict:
    return MessageToDict(message, preserving_proto_field_name=True)


def task_summary(task, *, reused: bool | None = None) -> dict:
    value = dump_message(task)
    state = value.get("status", {}).get("state", "TASK_STATE_UNSPECIFIED")
    result = {"task_id": value.get("id"), "state": state, "terminal": state in TERMINAL, "task": value}
    if reused is not None:
        result["idempotency_reused"] = reused
    return result


def append_event(state_path: Path, event: dict) -> None:
    event_path = state_path.with_suffix(".events.jsonl")
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": time.time(), **event}, separators=(",", ":")) + "\n")


def locked_state(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    handle.seek(0)
    try:
        value = json.load(handle)
    except (json.JSONDecodeError, ValueError):
        value = {"requests": {}}
    value.setdefault("requests", {})
    return handle, value


def save_state(handle, value: dict) -> None:
    handle.seek(0)
    handle.truncate()
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()


async def open_client(base_url: str):
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
    card = await A2ACardResolver(httpx_client=http_client, base_url=base_url).get_agent_card()
    client = await create_client(agent=card, client_config=ClientConfig(streaming=False, polling=True))
    return http_client, client


async def get_task(base_url: str, task_id: str):
    http_client, client = await open_client(base_url)
    try:
        return await client.get_task(GetTaskRequest(id=task_id, history_length=10))
    finally:
        await client.close()
        await http_client.aclose()


async def submit(state_path: Path, base_url: str, key: str, message: str) -> dict:
    digest = hashlib.sha256(message.encode()).hexdigest()
    registry_key = f"{base_url}|{key}"
    handle, state = locked_state(state_path)
    try:
        prior = state["requests"].get(registry_key)
        if prior:
            if prior["message_sha256"] != digest:
                raise ValueError("idempotency key was already used for a different message")
            task = await get_task(base_url, prior["task_id"])
            result = task_summary(task, reused=True)
            append_event(state_path, {"event": "submit_reused", "key": key, **{k: result[k] for k in ("task_id", "state")}})
            return result

        metadata = Struct()
        metadata.update({"idempotency_key": key, "retry_owner": "coordinator"})
        request = SendMessageRequest(
            message=new_text_message(message, role=Role.ROLE_USER),
            configuration=SendMessageConfiguration(return_immediately=True, history_length=10),
            metadata=metadata,
        )
        http_client, client = await open_client(base_url)
        try:
            response = None
            async for item in client.send_message(request):
                response = item
                break
        finally:
            await client.close()
            await http_client.aclose()
        task = response.task if response is not None and response.HasField("task") else None
        if task is None or not task.id:
            raise RuntimeError("A2A submission did not return a task id")
        state["requests"][registry_key] = {
            "base_url": base_url, "idempotency_key": key, "message_sha256": digest,
            "task_id": task.id, "submitted_at": time.time(), "retry_owner": "coordinator",
        }
        save_state(handle, state)
        result = task_summary(task, reused=False)
        append_event(state_path, {"event": "submitted", "key": key, **{k: result[k] for k in ("task_id", "state")}})
        return result
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


async def poll(state_path: Path, base_url: str, key: str) -> dict:
    handle, state = locked_state(state_path)
    try:
        record = state["requests"].get(f"{base_url}|{key}")
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
    if not record:
        raise KeyError("unknown idempotency key; only the coordinator may decide to submit or retry work")
    task = await get_task(base_url, record["task_id"])
    result = task_summary(task)
    append_event(state_path, {"event": "polled", "key": key, **{k: result[k] for k in ("task_id", "state", "terminal")}})
    return result


async def wait(state_path: Path, base_url: str, key: str, timeout: float, interval: float) -> dict:
    deadline = time.monotonic() + timeout
    transient_failures = 0
    while True:
        try:
            result = await poll(state_path, base_url, key)
            transient_failures = 0
            if result["terminal"]:
                return result
        except (httpx.HTTPError, TimeoutError) as exc:
            transient_failures += 1
            append_event(state_path, {"event": "poll_retry", "key": key, "attempt": transient_failures, "error": type(exc).__name__})
            if transient_failures > 3:
                raise
        if time.monotonic() >= deadline:
            raise TimeoutError("poll deadline exceeded; task was not resubmitted")
        await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    submit_parser = sub.add_parser("submit")
    submit_parser.add_argument("base_url")
    submit_parser.add_argument("key")
    submit_parser.add_argument("message")
    poll_parser = sub.add_parser("poll")
    poll_parser.add_argument("base_url")
    poll_parser.add_argument("key")
    wait_parser = sub.add_parser("wait")
    wait_parser.add_argument("base_url")
    wait_parser.add_argument("key")
    wait_parser.add_argument("--timeout", type=float, default=1200)
    wait_parser.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    if args.command == "submit":
        result = asyncio.run(submit(args.state, args.base_url, args.key, args.message))
    elif args.command == "poll":
        result = asyncio.run(poll(args.state, args.base_url, args.key))
    else:
        result = asyncio.run(wait(args.state, args.base_url, args.key, args.timeout, args.interval))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
