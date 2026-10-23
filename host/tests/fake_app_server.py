"""Synthetic JSONL peer. No real account, model, files or approvals."""

import json
import sys
import time

mode = sys.argv[1]


def send(value):
    print(json.dumps(value), flush=True)


for raw in sys.stdin:
    request = json.loads(raw)
    if request["method"] == "initialized":
        if mode == "no_read_after_init":
            time.sleep(30)
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
    elif mode == "error":
        send({"id": request_id, "error": {"message": "SECRET_DIAGNOSTIC"}})
    elif mode == "server_request":
        send({"id": "approval_demo", "method": "unsupported/request", "params": {}})
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
