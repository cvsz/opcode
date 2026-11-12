import sys

import requests


def test_fuzz():
    url = "http://127.0.0.1:8765/api/run"
    token = sys.argv[1] if len(sys.argv) > 1 else ""
    headers = {"X-Router-Token": token, "Content-Type": "application/json"}
    
    # Oversized prompt
    payload = {"task": "code", "prompt": "a" * 200000}
    r = requests.post(url, json=payload, headers=headers)
    print(f"Oversized prompt status: {r.status_code}")
    
    # Negative timeout
    payload = {"task": "code", "prompt": "hi", "timeout": -1}
    r = requests.post(url, json=payload, headers=headers)
    print(f"Negative timeout status: {r.status_code}")
    
    # Invalid task type
    payload = {"task": "invalid_task_type_here", "prompt": "hi"}
    r = requests.post(url, json=payload, headers=headers)
    print(f"Invalid task status: {r.status_code}")

if __name__ == "__main__":
    test_fuzz()
