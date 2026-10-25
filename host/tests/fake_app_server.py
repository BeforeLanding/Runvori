"""Synthetic JSONL peer. No real account, model, files or approvals."""

import json
import sys
import time

mode = sys.argv[1]


def send(value):
    print(json.dumps(value), flush=True)


for raw in sys.stdin:
    request = json.loads(raw)
    if "method" not in request:
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
              "params": {"threadId": "thread_demo", "turnId": "turn_demo"}})
    elif mode == "turn_terminal" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo",
            "turn": {"id": "turn_demo", "status": "completed"}}})
    elif mode == "event_handoff" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": 23, "method": "item/commandExecution/requestApproval",
              "params": {"threadId": "thread_demo", "turnId": "turn_demo"}})
        send({"method": "turn/completed", "params": {
            "threadId": "thread_demo",
            "turn": {"id": "turn_demo", "status": "completed"}}})
        send({"method": "serverRequest/resolved", "params": {
            "threadId": "thread_demo", "requestId": 23}})
    elif mode == "approval_before_response" and request["method"] == "turn/start":
        send({"id": "approval_demo", "method": "item/fileChange/requestApproval",
              "params": {"threadId": "thread_demo", "turnId": "turn_demo"}})
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
              "params": {"threadId": "thread_demo", "turnId": "turn_demo",
                         "itemId": "patch_demo", "startedAtMs": 1}})
    elif mode == "approval_resolved" and request["method"] == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn_demo"}}})
        send({"id": 23, "method": "item/commandExecution/requestApproval",
              "params": {"threadId": "thread_demo", "turnId": "turn_demo"}})
        send({"method": "serverRequest/resolved", "params": {
            "threadId": "thread_demo", "requestId": 23}})
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
