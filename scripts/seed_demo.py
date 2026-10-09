"""Submit a small 50-recipient example batch to a running API."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> None:
    """Submit the demo batch and print its status and download URLs."""
    base_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    payload = {
        "event_title": "Practical Python Workshop",
        "issuer_name": "Northstar Academy",
        "issue_date": "2026-10-01",
        "description": "Completion of the two-day practical Python workshop.",
        "recipients": [
            {"name": f"Demo Learner {index:02d}", "email": f"learner{index:02d}@example.org"}
            for index in range(1, 51)
        ],
    }
    request = Request(
        f"{base_url}/api/batches",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
            print(json.dumps(result, indent=2))
    except (HTTPError, URLError) as exc:
        raise SystemExit(f"Could not submit demo batch: {exc}") from exc


if __name__ == "__main__":
    main()
