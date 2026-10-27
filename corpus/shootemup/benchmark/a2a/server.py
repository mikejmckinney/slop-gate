#!/usr/bin/env python3
"""A2A 1.0 JSON-RPC server that exposes an opaque coding agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from starlette.applications import Starlette


ROLE_PROMPTS = {
    "gameplay": (
        "You are the gameplay/frontend specialist in an A2A team. Implement only the substantive "
        "work requested in the A2A message, inspect existing shared-repository work first, verify it, "
        "and leave the shared tree integration-ready. Do not spawn subagents."
    ),
    "platform": (
        "You are the data/platform/quality specialist in an A2A team. Implement only the substantive "
        "work requested in the A2A message, inspect existing shared-repository work first, verify it, "
        "and leave the shared tree integration-ready. Do not spawn subagents."
    ),
}


class CodingA2AExecutor(AgentExecutor):
    def __init__(self, candidate_dir: Path, raw_dir: Path, role: str, runtime: str, model: str, effort: str) -> None:
        self.candidate_dir = candidate_dir
        self.raw_dir = raw_dir
        self.role = role
        self.runtime = runtime
        self.model = model
        self.effort = effort
        self.sequence = 0
        self.lock = asyncio.Lock()

    def _append_protocol_log(self, value: dict) -> None:
        log_path = self.raw_dir / "a2a-protocol.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, separators=(",", ":")) + "\n")

    def _invoke_agent(self, request_text: str, request_id: str) -> tuple[str, int, float]:
        self.sequence += 1
        stem = f"worker-{self.role}-{self.sequence:02d}-{request_id[:8]}"
        session_path = self.raw_dir / f"{stem}.jsonl"
        stderr_path = self.raw_dir / f"{stem}.stderr.log"
        final_path = self.raw_dir / f"{stem}.final.txt"
        prompt = (
            f"{ROLE_PROMPTS[self.role]}\n\n"
            "The complete product contract is in BENCHMARK_TASK.md. You are communicating with the "
            "coordinator through the Linux Foundation A2A protocol; your internal state is not shared.\n\n"
            f"A2A task from coordinator:\n{request_text}"
        )
        if self.runtime == "opencode":
            command = [
                "/home/codespace/.opencode/bin/opencode", "run", "--format", "json", "--auto",
                "--dir", str(self.candidate_dir), "--model", self.model,
                "--variant", self.effort, prompt,
            ]
        else:
            command = [
                "codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                "--cd", str(self.candidate_dir), "--model", self.model,
                "-c", f'model_reasoning_effort="{self.effort}"', "-c", "features.multi_agent=false",
                "--output-last-message", str(final_path), prompt,
            ]
        started = time.monotonic()
        with session_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            try:
                completed = subprocess.run(
                    command,
                    cwd=self.candidate_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    timeout=1200,
                    check=False,
                )
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                exit_code = 124
        elapsed = time.monotonic() - started
        final_text = final_path.read_text(encoding="utf-8").strip() if final_path.exists() else ""
        if self.runtime == "opencode" and session_path.exists():
            for line in session_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "text" and event.get("part", {}).get("text"):
                    final_text = event["part"]["text"].strip()
        if not final_text:
            final_text = f"{self.role} worker ended with exit code {exit_code}; inspect shared files and worker logs."
        return final_text, exit_code, elapsed

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message(f"{self.role} agent is working"),
        )
        request_text = get_message_text(context.message) or ""
        request_id = str(uuid.uuid4())
        self._append_protocol_log({
            "event": "message_received", "timestamp": time.time(), "request_id": request_id,
            "role": self.role, "task_id": task.id, "text": request_text,
        })
        async with self.lock:
            final_text, exit_code, elapsed = await asyncio.to_thread(self._invoke_agent, request_text, request_id)
        self._append_protocol_log({
            "event": "message_completed", "timestamp": time.time(), "request_id": request_id,
            "role": self.role, "task_id": task.id, "exit_code": exit_code,
            "elapsed_seconds": elapsed, "response": final_text,
        })
        await updater.add_artifact(parts=[new_text_part(text=final_text, media_type="text/plain")])
        state = TaskState.TASK_STATE_COMPLETED if exit_code == 0 else TaskState.TASK_STATE_FAILED
        await updater.update_status(
            state=state,
            message=new_text_message(f"{self.role} agent finished with exit code {exit_code}"),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=sorted(ROLE_PROMPTS))
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--runtime", choices=["codex", "opencode"], default="codex")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="max")
    args = parser.parse_args()
    args.raw_dir.mkdir(parents=True, exist_ok=True)

    skill = AgentSkill(
        id=f"shootemup_{args.role}", name=f"Neon Barrage {args.role} specialist",
        description=ROLE_PROMPTS[args.role], input_modes=["text/plain"], output_modes=["text/plain"],
        tags=["software-delivery", "shootemup", args.role],
    )
    card = AgentCard(
        name=f"Luna Max {args.role} agent", description=ROLE_PROMPTS[args.role], version="1.0.0",
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=args.runtime == "opencode"),
        supported_interfaces=[AgentInterface(
            protocol_binding="JSONRPC", url=f"http://127.0.0.1:{args.port}", protocol_version="1.0"
        )],
        skills=[skill],
    )
    handler = DefaultRequestHandler(
        agent_executor=CodingA2AExecutor(
            args.candidate_dir, args.raw_dir, args.role, args.runtime, args.model, args.effort
        ),
        task_store=InMemoryTaskStore(), agent_card=card,
    )
    routes = [*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")]
    uvicorn.run(Starlette(routes=routes), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
