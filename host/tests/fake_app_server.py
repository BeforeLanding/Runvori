"""Synthetic JSONL peer. No real account, model, files or approvals."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
HOST_MODES = {
    "host_turn_start_hang", "host_running", "host_approval",
    "host_approval_dispatch", "host_interrupt_hang", "host_turn_exit",
}
# Modes driven over the product HTTPS API; they answer the approval and finish.
# ``host_api_flow`` chooses the approval kind from the turn input text so one
# Host process can demonstrate both the reviewable and the insufficient case.
API_MODES = {"host_api_flow", "host_api_no_approval", "host_api_device"}
fake_turns: list = []
thread_starts = 0
pending_approvals: dict = {}
# Which upstream item each fake turn's approval belongs to, so the post-answer
# output events name the same item identity the approval request used.
pending_items: dict = {}
item_ids: dict = {}
# The thread each synthetic turn was started on: a second session has its own,
# so a notification must name the thread the Host actually addressed.
turn_threads: dict = {}
# What each pending approval should do when answered (acceptance scenarios).
pending_scenarios: dict = {}
# The command result each synthetic item should emit after approval.
pending_command_results: dict = {}
# A synthetic command approval names the workspace only when the launcher set
# RUNVORI_FAKE_WORKSPACE. Without it the review is (correctly) unusable for
# allow, so tests that approve must opt in explicitly.
workspace = os.environ.get("RUNVORI_FAKE_WORKSPACE") or None
cut_marker = Path(sys.argv[2]) if mode in HOST_MODES and len(sys.argv) > 2 else None
leaf_marker = Path(sys.argv[3]) if mode in HOST_MODES and len(sys.argv) > 3 else None


def send(value):
    print(json.dumps(value), flush=True)


def approval_params(extra=None, turn_id="turn_demo", item_id="item_demo",
                    thread_id="thread_demo"):
    """A command approval in the observed upstream shape."""
    params = {"threadId": thread_id, "turnId": turn_id,
              "itemId": item_id, "startedAtMs": 1700000000000,
              "kind": "command", "command": "python --version",
              "reason": "synthetic probe approval"}
    if workspace is not None:
        params["cwd"] = workspace
    if extra:
        params.update(extra)
    return params


def file_approval_params(extra=None, turn_id="turn_demo",
                         item_id="patch_demo", thread_id="thread_demo"):
    params = {"threadId": thread_id, "turnId": turn_id,
              "itemId": item_id, "startedAtMs": 1700000000001}
    if extra:
        params.update(extra)
    return params


def file_item(item_id="patch_demo", path="probe-result.txt"):
    return {"id": item_id, "type": "fileChange", "status": "inProgress",
            "changes": [{"path": path, "kind": {"type": "add"},
                         "diff": "+RUNVORI_PROBE_OK"}]}


def mark(path, text):
    if path is None:
        return
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(text, encoding="ascii")
    os.replace(staging, path)


if mode in HOST_MODES:
    mark(Path(str(cut_marker) + ".peer"), str(os.getpid()))
    if leaf_marker is not None:
        # The leaf must not inherit the protocol pipes: a surviving descendant
        # would otherwise keep the Host's stdio read open after this peer exits.
        subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name(
                "process_tree_peer.py")), "leaf", str(leaf_marker)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)


for raw in sys.stdin:
    request = json.loads(raw)
    if "method" not in request:
        if mode in API_MODES:
            turn_id = pending_approvals.pop(request["id"], None)
            if turn_id is None:
                continue
            scenario = pending_scenarios.pop(request["id"], ("allow", ""))
            thread_id = turn_threads.get(turn_id, "thread_demo")
            if scenario[0] == "expire":
                # A late answer must not revive a request the Host already
                # recorded as closed; nothing is sent back for it.
                continue
            if scenario[0] == "interrupt":
                # The turn ends as interrupted without a command result.
                send({"method": "serverRequest/resolved", "params": {
                    "threadId": thread_id, "requestId": request["id"]}})
                send({"method": "turn/completed", "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "interrupted"}}})
                continue
            if scenario[0] == "deny_file":
                # A denied file change writes nothing: close the request and
                # end the turn without inventing command output.
                send({"method": "serverRequest/resolved", "params": {
                    "threadId": thread_id, "requestId": request["id"]}})
                send({"method": "turn/completed", "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "completed"}}})
                continue
            send({"method": "serverRequest/resolved", "params": {
                "threadId": thread_id, "requestId": request["id"]}})
            # The approved command "ran": emit its output and result, then a
            # final agent message that streams as deltas before completing.
            answer_item = pending_items.pop(turn_id, "item_demo")
            result = pending_command_results.pop(answer_item, {
                "command": "python --version", "output": "python 3.12.10\n",
                "exit_code": 0, "status": "completed"})
            output = result["output"]
            if output:
                send({"method": "item/commandExecution/outputDelta", "params": {
                    "threadId": thread_id, "turnId": turn_id,
                    "itemId": answer_item, "delta": output[:5]}})
                send({"method": "item/commandExecution/outputDelta", "params": {
                    "threadId": thread_id, "turnId": turn_id,
                    "itemId": answer_item, "delta": output[5:]}})
            send({"method": "item/completed", "params": {
                "threadId": thread_id, "turnId": turn_id,
                "completedAtMs": 1700000000120,
                "item": {"type": "commandExecution",
                         "id": answer_item,
                         "command": result["command"],
                         "cwd": workspace or "",
                         "commandActions": [],
                         "status": result["status"],
                         "exitCode": result["exit_code"], "durationMs": 12}}})
            send({"method": "item/started", "params": {
                "threadId": thread_id, "turnId": turn_id,
                "item": {"type": "agentMessage", "id": "msg_demo",
                         "text": ""}}})
            for piece in ("合成完", "成。"):
                send({"method": "item/agentMessage/delta", "params": {
                    "threadId": thread_id, "turnId": turn_id,
                    "itemId": "msg_demo", "delta": piece}})
            send({"method": "item/completed", "params": {
                "threadId": thread_id, "turnId": turn_id,
                "item": {"type": "agentMessage", "id": "msg_demo",
                         "text": "合成完成。"}}})
            send({"method": "turn/completed", "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed"}}})
            continue
        if mode == "host_approval_dispatch":
            mark(Path(str(cut_marker) + ".answers"), "answer_received\n")
            mark(cut_marker, "approval_answered")
            time.sleep(120)
            continue
        if mode in ("approval_ack_lost_before_effect", "approval_ack_lost_after_effect"):
            with open(sys.argv[2], "a", encoding="utf-8") as received:
                received.write("answer_received\n")
            if mode == "approval_ack_lost_after_effect":
                with open(sys.argv[3], "w", encoding="utf-8") as effect:
                    effect.write("effect_applied\n")
            sys.exit(0)
        if mode in ("approval", "approval_before_response"):
            send({"method": "synthetic/approvalAnswered", "params": {
                "id": request["id"], "decision": request["result"]["decision"]}})
            send({"method": "serverRequest/resolved", "params": {
                "threadId": "thread_demo", "requestId": request["id"]}})
        continue
    if request["method"] == "initialized":
        if mode == "no_read_after_init":
            time.sleep(30)
        if mode == "exit_after_initialized":
            sys.exit(3)
        continue
    request_id = request["id"]
    if mode == "hang":
        time.sleep(30)
    elif mode == "exit":
        sys.exit(3)
    elif mode == "invalid":
        print("not-json", flush=True)
    elif mode == "array":
        send([])
    elif mode == "oversized":
        print("x" * 5000, flush=True)
    elif mode == "wrong_id":
        send({"id": request_id + 1, "result": {}})
    elif mode == "boolean_id":
        send({"id": True, "result": {}})
    elif mode == "both":
        send({"id": request_id, "result": {}, "error": {}})
    elif mode == "scalar_result":
        send({"id": request_id, "result": "secret should not be logged"})
    elif mode == "recoverable_rpc_error" and request["method"] == "turn/interrupt":
        send({"id": request_id, "error": {"message": "SECRET_DIAGNOSTIC"}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo", "turn": {"id": "turn_demo", "status": "completed"}}})
    elif mode == "interrupt_ack_then_terminal" and request["method"] == "turn/interrupt":
        send({"id": request_id, "result": {}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo", "turn": {"id": "turn_demo", "status": "interrupted"}}})
    elif mode == "interrupt_lost_reply" and request["method"] == "turn/interrupt":
        sys.exit(0)
    elif mode == "error":
        send({"id": request_id, "error": {"message": "SECRET_DIAGNOSTIC"}})
    elif mode == "server_request":
        send({"id": "approval_demo", "method": "unsupported/request", "params": {}})
    elif request["method"] == "thread/resume" and mode in (
            "approval_before_response", "turn_start_no_response",
            "turn_start_bad_shape"):
        send({"id": request_id, "result": {"thread": {"id": "thread_demo"}}})
    elif mode == "approval" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": "approval_demo", "method": "item/commandExecution/requestApproval",
              "params": approval_params()})
    elif mode == "turn_terminal" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo",
            "turn": {"id": "turn_demo", "status": "completed"}}})
    elif mode == "event_handoff" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": 23, "method": "item/commandExecution/requestApproval",
              "params": approval_params()})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo",
            "turn": {"id": "turn_demo", "status": "completed"}}})
        send({"method": "serverRequest/resolved", "params": {
            "threadId": "thread_demo", "requestId": 23}})
    elif mode == "approval_before_response" and request["method"] == "turn/start":
        send({"id": "approval_demo", "method": "item/fileChange/requestApproval",
              "params": file_approval_params()})
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
    elif mode == "turn_start_no_response" and request["method"] == "turn/start":
        sys.exit(0)
    elif mode == "turn_start_bad_shape" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {}}})
    elif mode == "thread_resume_good" and request["method"] == "thread/resume":
        send({"method": "synthetic/resumeNotice", "params": {"threadId": "thread_demo"}})
        send({"id": request_id, "result": {"thread": {"id": "thread_demo"}}})
    elif mode == "thread_resume_wrong" and request["method"] == "thread/resume":
        send({"id": request_id, "result": {"thread": {"id": "other_thread"}}})
    elif mode == "thread_resume_no_response" and request["method"] == "thread/resume":
        sys.exit(0)
    elif mode == "thread_bootstrap_good" and request["method"] == "thread/start":
        send({"id": request_id, "result": {"thread": {"id": "thread_demo"}}})
    elif mode == "thread_bootstrap_bad_shape" and request["method"] == "thread/start":
        send({"id": request_id, "result": {"thread": {}}})
    elif mode == "thread_bootstrap_lost" and request["method"] == "thread/start":
        with open(sys.argv[2], "w", encoding="utf-8") as marker:
            marker.write("created\n")
        sys.exit(0)
    elif mode in ("approval_ack_lost_before_effect", "approval_ack_lost_after_effect") \
            and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": "approval_demo", "method": "item/fileChange/requestApproval",
              "params": file_approval_params()})
    elif mode == "approval_resolved" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": 23, "method": "item/commandExecution/requestApproval",
              "params": approval_params()})
        send({"method": "serverRequest/resolved", "params": {
            "threadId": "thread_demo", "requestId": 23}})
    elif mode in HOST_MODES and request["method"] == "thread/resume":
        send({"id": request_id, "result": {"thread": {"id": "thread_demo"}}})
    elif mode == "host_turn_start_hang" and request["method"] == "turn/start":
        # The Host claims dispatch before sending this request.
        mark(cut_marker, "turn_start")
        time.sleep(120)
    elif mode == "host_running" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        mark(cut_marker, "running")
    elif mode in ("host_approval", "host_approval_dispatch") \
            and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": "approval_demo",
              "method": "item/commandExecution/requestApproval",
              "params": approval_params()})
        if mode == "host_approval":
            mark(cut_marker, "approval")
    elif mode == "host_interrupt_hang" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
    elif mode == "host_interrupt_hang" and request["method"] == "turn/interrupt":
        mark(cut_marker, "interrupt")
        time.sleep(120)
    elif mode == "host_turn_exit" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        time.sleep(0.5)
        sys.exit(0)
    elif mode in API_MODES and request["method"] == "thread/start":
        # The real app-server issues a fresh thread id per start; reusing one
        # would make two sessions collide and the Host would (correctly)
        # recover the upstream, ending the run. The first id keeps the name
        # the single-session tests expect.
        thread_starts += 1
        thread_id = "thread_demo" if thread_starts == 1 \
            else f"thread_demo_{thread_starts}"
        send({"id": request_id, "result": {"thread": {"id": thread_id}}})
    elif mode in API_MODES and request["method"] == "thread/resume":
        send({"id": request_id, "result": {"thread": {"id": "thread_demo"}}})
    elif mode == "host_api_no_approval" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo",
            "turn": {"id": "turn_demo", "status": "completed"}}})
    elif mode == "host_api_device" and request["method"] == "turn/start":
        prompt = json.dumps(request.get("params", {}))
        # Echo the thread the Host addressed; a second session has its own id.
        thread_id = request.get("params", {}).get("threadId", "thread_demo")
        fake_turns.append(None)
        index = len(fake_turns)
        turn_id = f"turn_device_{index}"
        approval_id = f"approval_device_{index}"
        item_id = f"item_device_{index}"
        failed = "[verify-fail]" in prompt
        command = "exit 7" if failed else "python --version"
        command_result = {
            "command": command,
            "output": "exit 7\n" if failed else "python 3.12.10\n",
            "exit_code": 7 if failed else 0,
            "status": "failed" if failed else "completed",
        }
        started_ms = 1700000000000 + index
        command_review = {"command": command, "startedAtMs": started_ms}
        pending_command_results[item_id] = command_result
        turn_threads[turn_id] = thread_id
        send({"id": request_id, "result": {"turn": {"id": turn_id}}})
        send({"method": "item/started", "params": {
            "threadId": thread_id, "turnId": turn_id,
            "startedAtMs": started_ms,
            "item": {"type": "commandExecution", "id": item_id,
                     "command": command,
                     "cwd": workspace or "",
                     "commandActions": [],
                     "status": "inProgress"}}})
        if "[expire]" in prompt:
            # The turn closes the request on its own, so the pending approval
            # is expired before the phone can decide.
            send({"id": approval_id,
                  "method": "item/commandExecution/requestApproval",
                  "params": approval_params(
                      turn_id=turn_id, item_id=item_id,
                      thread_id=thread_id, extra=command_review)})
            send({"method": "serverRequest/resolved", "params": {
                "threadId": thread_id, "requestId": approval_id}})
            send({"method": "turn/completed", "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed"}}})
            continue
        if "[expire-request]" in prompt:
            # The Host is told the request is closed while it is still
            # pending; a decision must then be refused as expired. Its own
            # turn also ends, so the Host's single active-run slot is free for
            # the next scenario.
            send({"id": approval_id,
                  "method": "item/commandExecution/requestApproval",
                  "params": approval_params(
                      turn_id=turn_id, item_id=item_id,
                      thread_id=thread_id, extra=command_review)})
            pending_approvals[approval_id] = turn_id
            pending_items[turn_id] = item_id
            send({"method": "serverRequest/resolved", "params": {
                "threadId": thread_id, "requestId": approval_id}})
            send({"method": "turn/completed", "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed"}}})
            continue
        if "[file-change]" in prompt:
            # A file change carries no command and no diff in this request, so
            # the review must stay insufficient: deny is the only decision the
            # product may offer.
            send({"id": approval_id,
                  "method": "item/fileChange/requestApproval",
                  "params": file_approval_params(
                      turn_id=turn_id, item_id=item_id,
                      thread_id=thread_id)})
            pending_approvals[approval_id] = turn_id
            pending_items[turn_id] = item_id
            pending_scenarios[approval_id] = ("deny_file", "")
            continue
        if "[interrupt]" in prompt:
            send({"id": approval_id,
                  "method": "item/commandExecution/requestApproval",
                  "params": approval_params(
                      turn_id=turn_id, item_id=item_id,
                      thread_id=thread_id, extra=command_review)})
            pending_approvals[approval_id] = turn_id
            pending_items[turn_id] = item_id
            pending_scenarios[approval_id] = ("interrupt", "")
            continue
        send({"id": approval_id,
              "method": "item/commandExecution/requestApproval",
              "params": approval_params(
                  turn_id=turn_id, item_id=item_id, thread_id=thread_id,
                  extra=command_review)})
        pending_approvals[approval_id] = turn_id
        pending_items[turn_id] = item_id
    elif mode == "host_api_device" and request["method"] == "turn/interrupt":
        # Acknowledge the RPC, then report the turn terminal separately: the
        # RPC response alone is not proof the turn stopped.
        send({"id": request_id, "result": {}})
        # Each device-mode turn is `turn_device_<index>`; interrupting answers
        # for the most recent one, which is the only possible active turn.
        turn_id = f"turn_device_{len(fake_turns)}"
        send({"method": "turn/completed", "params": {
            "threadId": turn_threads.get(turn_id, "thread_demo"),
            "turn": {"id": turn_id, "status": "interrupted"}}})
    elif mode == "host_api_flow" and request["method"] == "turn/start":
        prompt = json.dumps(request.get("params", {}))
        # Each accepted turn needs its own upstream turn identity; reusing one
        # id would collide with the Host's (epoch, thread, turn) binding.
        fake_turns.append(None)
        turn_id = f"turn_demo_{len(fake_turns)}"
        approval_id = f"approval_demo_{len(fake_turns)}"
        send({"id": request_id, "result": {"turn": {"id": turn_id}}})
        item_id = f"item_demo_{len(fake_turns)}"
        item_ids[turn_id] = item_id
        # The item opens before the approval request, as observed upstream: a
        # started command item is what makes the timeline entry available.
        send({"method": "item/started", "params": {
            "threadId": "thread_demo", "turnId": turn_id,
            "item": {"type": "commandExecution", "id": item_id,
                     "command": "python --version",
                     "cwd": workspace or "",
                     "commandActions": [],
                     "status": "inProgress"}}})
        if "file-change" in prompt:
            # No item/started is emitted: the review must stay insufficient.
            send({"id": approval_id,
                  "method": "item/fileChange/requestApproval",
                  "params": file_approval_params(
                      turn_id=turn_id, item_id=item_id)})
        else:
            send({"id": approval_id,
                  "method": "item/commandExecution/requestApproval",
                  "params": approval_params(
                      turn_id=turn_id, item_id=item_id,
                      thread_id=thread_id)})
        pending_approvals[approval_id] = turn_id
        pending_items[turn_id] = item_id
    else:
        if mode == "flood":
            for i in range(1000):
                send({"method": "synthetic/event", "params": {"n": i}})
        if mode == "notification":
            send({"method": "synthetic/event", "params": {"text": "中文"}})
        if mode == "stderr":
            sys.stderr.write("s" * 200_000)
            sys.stderr.flush()
        send({"id": request_id, "result": {"method": request["method"]}})
