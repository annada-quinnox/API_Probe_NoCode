import csv
import json
import os
import re
import subprocess
import time
from performance.caching import run_etag_caching_test
from performance.load_factor import build_load_factor_script
import requests


class PerformanceExecutionError(Exception):
    """Raised when performance execution fails."""


def parse_query_params(input_data):
    if not isinstance(input_data, str):
        return input_data if isinstance(input_data, dict) else {}

    qs = input_data.strip()

    if qs.startswith('?'):
        qs = qs[1:]

    if ' ' in qs:
        parts = qs.split(' ')
        for part in parts:
            if '?' in part:
                qs = part.split('?')[1]
                break
            elif '=' in part:
                qs = part
                break

    elif '?' in qs:
        qs = qs.split('?')[1]

    params = {}

    if not qs or '=' not in qs:
        return params

    for pair in qs.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)

            if value.lower() == 'true':
                value = True
            elif value.lower() == 'false':
                value = False
            elif value.isdigit():
                value = int(value)
            else:
                try:
                    value = float(value)
                except ValueError:
                    pass

            params[key] = value

    return params


def extract_response_code(expected_input):
    if not expected_input:
        return ["N/A"]

    if isinstance(expected_input, list):
        valid_codes = []

        for item in expected_input:
            if item is None:
                continue

            item_str = str(item).strip()

            if re.match(r'^[1-5]\d{2}$', item_str):
                valid_codes.append(item_str)

            elif item_str.upper() != "N/A":
                matches = re.findall(r'\b([1-5]\d{2})\b', item_str)
                valid_codes.extend(matches)

        return valid_codes if valid_codes else ["N/A"]

    expected_str = str(expected_input)
    expected_lower = expected_str.lower()

    if 'default sort' in expected_lower or 'fallback' in expected_lower:
        codes = re.findall(r'\b([1-5]\d{2})\b', expected_str)

        normalized = []

        for code in codes:
            if code not in normalized:
                normalized.append(code)

        if '200' not in normalized:
            normalized.append('200')

        if normalized:
            return normalized

    matches = re.findall(r'\b([1-5]\d{2})\b', expected_str)

    if matches:
        return matches

    if 'created' in expected_lower:
        return ["201"]

    if 'no content' in expected_lower:
        return ["204"]

    if 'success' in expected_lower or 'ok' in expected_lower:
        return ["200"]

    if (
        'bad request' in expected_lower
        or 'invalid' in expected_lower
        or 'missing' in expected_lower
    ):
        return ["400"]

    if 'unauthorized' in expected_lower:
        return ["401"]

    if 'forbidden' in expected_lower:
        return ["403"]

    if 'not found' in expected_lower:
        return ["404"]

    if 'conflict' in expected_lower:
        return ["409"]

    if (
        'too many' in expected_lower
        or 'rate limit' in expected_lower
    ):
        return ["429"]

    return ["N/A"]

PERCENTILE_THRESHOLDS = {
    "p50": 500,
    "p95": 1000,
    "p99": 2000,
}


def _parse_percentile(value):
    """
    Convert a Locust percentile CSV value to milliseconds.
    Returns None when the value cannot be evaluated.
    """
    if value is None:
        return None

    text = str(value).strip().replace(",", "")

    if not text or text.upper() in {"N/A", "NA", "NONE"}:
        return None

    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _build_locust_metrics(row):
    """
    Build the Locust metrics returned to the frontend,
    including P50/P95/P99 SLA evaluation.
    """

    p50 = _parse_percentile(row.get("50%"))
    p95 = _parse_percentile(row.get("95%"))
    p99 = _parse_percentile(row.get("99%"))

    p50_pass = (
        p50 is not None
        and p50 < PERCENTILE_THRESHOLDS["p50"]
    )

    p95_pass = (
        p95 is not None
        and p95 < PERCENTILE_THRESHOLDS["p95"]
    )

    p99_pass = (
        p99 is not None
        and p99 < PERCENTILE_THRESHOLDS["p99"]
    )

    return {
        "requests_made": row.get(
            "Request Count",
            "0"
        ),

        "failures": row.get(
            "Failure Count",
            "0"
        ),

        "median_ms": row.get(
            "Median Response Time",
            "0"
        ),

        "avg_ms": row.get(
            "Average Response Time",
            "0"
        ),

        "max_ms": row.get(
            "Max Response Time",
            "0"
        ),

        "rps": row.get(
            "Requests/s",
            "0"
        ),

        "p50_ms": (
            p50 if p50 is not None
            else "N/A"
        ),

        "p95_ms": (
            p95 if p95 is not None
            else "N/A"
        ),

        "p99_ms": (
            p99 if p99 is not None
            else "N/A"
        ),

        "p50_pass": p50_pass,
        "p95_pass": p95_pass,
        "p99_pass": p99_pass,

        "percentile_passed": (
            p50_pass
            and p95_pass
            and p99_pass
        )
    }

def build_url(endpoint, base_url):
    base_url = (base_url or "").rstrip('/')
    endpoint = endpoint or '/'

    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint

    return base_url + endpoint


def run_performance_test(data):
    """
    Executes the existing performance-testing implementation.

    This is intentionally kept behavior-compatible with the
    current implementation in app.py. Performance-type-specific
    separation will be introduced in the next phase.
    """

    base_url = data.get('baseUrl', 'mock')

    if base_url == 'mock' or not base_url:
        return {
            "success": False,
            "error": "Performance testing requires a real Base URL, not a mock environment."
        }, 400

    test_case = data.get('testCase', {})

    method = test_case.get(
        'method',
        data.get('method', 'GET')
    ).upper()

    expected = test_case.get('expected', '200')
    expected_codes = extract_response_code(expected)

    current_endpoint = test_case.get(
        'endpoint',
        data.get('endpoint', '/search')
    )

    payload = test_case.get('input', {})

    performance_config = data.get('performanceConfig') or {}
    performance_type = performance_config.get('type')

    is_performance_test = performance_type in [
        'rate_limit',
        'load_factor',
        'stress_factor',
        'endurance',
        'caching'
    ]

    if method in ['GET', 'DELETE'] and not is_performance_test:

        if isinstance(payload, str):

            if payload.startswith('/'):
                current_endpoint = payload
                payload = {}

            elif payload.startswith('?') or '=' in payload:
                payload = parse_query_params(payload)

            else:
                current_endpoint = (
                    current_endpoint.rstrip('/')
                    + '/'
                    + payload
                )
                payload = {}

    # ---------------------------------------------------------
    # Existing Rate Limit handling
    # ---------------------------------------------------------

    if "429" in expected_codes:

        requests_made = 0
        latencies = []
        got_429 = False
        sample_output = "No output available"

        ping_url = build_url(
            current_endpoint,
            base_url
        )

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        for _ in range(6):

            start_time = time.time()

            try:

                if method in ["GET", "DELETE"]:

                    ping_res = requests.request(
                        method,
                        ping_url,
                        params=(
                            payload
                            if isinstance(payload, dict)
                            else None
                        ),
                        headers=headers,
                        timeout=5.0
                    )

                else:

                    if isinstance(payload, dict) and payload:

                        ping_res = requests.request(
                            method,
                            ping_url,
                            json=payload,
                            headers=headers,
                            timeout=5.0
                        )

                    else:

                        ping_res = requests.request(
                            method,
                            ping_url,
                            data=payload,
                            headers=headers,
                            timeout=5.0
                        )

                requests_made += 1

                latencies.append(
                    (time.time() - start_time) * 1000
                )

                if ping_res.status_code == 429:

                    got_429 = True
                    sample_output = ping_res.text[:1500]
                    break

                sample_output = ping_res.text[:1500]

            except Exception as exc:

                sample_output = str(exc)

        metrics = {
            "requests_made": str(requests_made),
            "failures": "0" if got_429 else "1",
            "median_ms": (
                str(round(sum(latencies) / len(latencies)))
                if latencies else "0"
            ),
            "avg_ms": (
                str(round(sum(latencies) / len(latencies)))
                if latencies else "0"
            ),
            "max_ms": (
                str(round(max(latencies)))
                if latencies else "0"
            ),
            "rps": "N/A (Rate Limit Mode)"
        }

        failure_details = []

        if not got_429:
            failure_details.append(
                "Rate limit of 5 requests per IP was NOT "
                "enforced by the server. 6th request succeeded."
            )

        return {
            "success": True,
            "metrics": metrics,
            "sample_output": sample_output,
            "failure_details": failure_details
        }, 200

    # ---------------------------------------------------------
    # Existing sample request
    # ---------------------------------------------------------

    safe_payload = json.dumps(payload)
    sample_output = "No output available"

    try:

        ping_url = build_url(
            current_endpoint,
            base_url
        )

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        if method in ["GET", "DELETE"]:

            ping_res = requests.request(
                method,
                ping_url,
                params=(
                    payload
                    if isinstance(payload, dict)
                    else None
                ),
                headers=headers,
                timeout=5.0
            )

        else:

            if isinstance(payload, dict) and payload:

                ping_res = requests.request(
                    method,
                    ping_url,
                    json=payload,
                    headers=headers,
                    timeout=5.0
                )

            else:

                ping_res = requests.request(
                    method,
                    ping_url,
                    data=payload,
                    headers=headers,
                    timeout=5.0
                )

        sample_output = ping_res.text[:1500]

    except Exception as exc:

        sample_output = (
            f"Failed to fetch sample: {str(exc)}"
        )

    # ---------------------------------------------------------
    # Performance configuration
    # ---------------------------------------------------------

    performance_config = (
        data.get('performanceConfig')
        or {}
    )

    performance_type = (
    performance_config.get('type')
    )

    if performance_type == 'caching':

        caching_enabled = str(
            performance_config.get('value', 'No')
        ).strip().lower()

        if caching_enabled != 'yes':

            return {
                "success": True,
                "metrics": {
                    "requests_made": "0",
                    "failures": "0",
                    "median_ms": "0",
                    "avg_ms": "0",
                    "max_ms": "0",
                    "rps": "0"
                },
                "sample_output": (
                    "Caching test skipped because "
                    "Caching configuration is set to No."
                ),
                "failure_details": []
            }, 200

        return run_etag_caching_test(
            base_url=base_url,
            endpoint=current_endpoint,
            method=method,
            payload=payload
        )

        # ---------------------------------------------------------
    # Load Factor
    # ---------------------------------------------------------

    if performance_type == 'load_factor':

        locust_script = build_load_factor_script(
            base_url=base_url,
            endpoint=current_endpoint,
            method=method,
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            payload=payload,
            expected_codes=expected_codes
        )

    users = performance_config.get('value')
    spawn_rate = performance_config.get('spawnRate')
    run_time = performance_config.get('runTime')

    if (
        users is None
        or spawn_rate is None
        or run_time is None
    ):

        return {
            "success": False,
            "error": (
                "Performance configuration is missing "
                f"for scenario: "
                f"{test_case.get('scenario', 'Unknown')}"
            )
        }, 400

    try:

        users = int(users)
        spawn_rate = float(spawn_rate)

    except (TypeError, ValueError):

        return {
            "success": False,
            "error": (
                "Invalid performance configuration. "
                "Users and spawn rate must be numeric."
            )
        }, 400

    if users <= 0:

        return {
            "success": False,
            "error": "Users must be greater than 0."
        }, 400

    if spawn_rate <= 0:

        return {
            "success": False,
            "error": "Spawn rate must be greater than 0."
        }, 400

    if not str(run_time).strip():

        return {
            "success": False,
            "error": "Run time cannot be empty."
        }, 400

    # ---------------------------------------------------------
    # Existing dynamic Locust script
    # ---------------------------------------------------------

    if performance_type == 'load_factor':

        locust_script = build_load_factor_script(
            base_url=base_url,
            endpoint=current_endpoint,
            method=method,
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            payload=payload,
            expected_codes=expected_codes
        )

    else:

        # Existing implementation for other performance types
        locust_script = f"""from locust import HttpUser, task, between
import json

class APIUser(HttpUser):

    wait_time = between(0.01, 0.05)

    host = "{base_url}"

    @task
    def execute_dynamic_request(self):

        headers = {{
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }}

        payload_raw = {safe_payload}

        try:
            payload_data = json.loads(payload_raw)
        except:
            payload_data = payload_raw

        kwargs = {{
            "headers": headers,
            "catch_response": True,
            "timeout": 15.0
        }}

        if "{method}" in ["GET", "DELETE"]:

            if isinstance(payload_data, dict) and payload_data:
                kwargs["params"] = payload_data

        else:

            if isinstance(payload_data, dict) and payload_data:
                kwargs["json"] = payload_data

            elif payload_data:
                kwargs["data"] = payload_data

        with self.client.request(
            "{method}",
            "{current_endpoint}",
            **kwargs
        ) as response:

            expected_codes = "{expected}"

            if "401" in expected_codes or "403" in expected_codes:

                if response.status_code in [401, 403]:
                    response.success()
                else:
                    response.failure(
                        f"Expected Auth Failure, got {{response.status_code}}"
                    )

            elif response.status_code in [
                200, 201, 202, 204, 304
            ]:

                response.success()

            else:

                response.failure(
                    f"Failed with HTTP {{response.status_code}}: "
                    f"{{response.text[:100]}}"
                )
"""

    timestamp = int(time.time() * 1000)

    csv_prefix = (
        f"perf_results_{timestamp}"
    )

    locust_file = (
        f"dynamic_locustfile_{timestamp}.py"
    )

    with open(
        locust_file,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(locust_script)

    print(
        f"\n🚀 Starting Locust Load Test on "
        f"{base_url}{current_endpoint} [{method}]..."
    )
    # Debug only.
    # print("\n" + "=" * 70)
    # print("LOCUST TARGET DEBUG")
    # print("=" * 70)
    # print("Base URL :", base_url)
    # print("Endpoint :", current_endpoint)
    # print("Full URL :", build_url(current_endpoint, base_url))
    # print("Method   :", method)
    # print("Expected :", expected)
    # print("Users    :", users)
    # print("Spawn    :", spawn_rate)
    # print("Run Time :", run_time)
    # print("=" * 70)
    command = [
        "locust",
        "-f",
        locust_file,
        "--headless",
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "--run-time",
        str(run_time),
        "--csv",
        csv_prefix
    ]

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    # Debug only.
    # print("\n--- LOCUST STDOUT ---")
    # print(process.stdout)
    #
    # print("\n--- LOCUST STDERR ---")
    # print(process.stderr)
    #
    # print("LOCUST EXIT CODE:", process.returncode)

    metrics = {}
    failure_details = []

    csv_file_stats = (
        f"{csv_prefix}_stats.csv"
    )

    csv_file_failures = (
        f"{csv_prefix}_failures.csv"
    )

    if os.path.exists(csv_file_failures):

        with open(
            csv_file_failures,
            mode='r',
            encoding='utf-8'
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                err_msg = row.get(
                    'Error',
                    ''
                )

                occ = row.get(
                    'Occurrences',
                    ''
                )

                if err_msg:

                    failure_details.append(
                        f"{err_msg} "
                        f"(Occurred {occ} times)"
                    )

    if os.path.exists(csv_file_stats):

        time.sleep(0.5)

        with open(
            csv_file_stats,
            mode='r',
            encoding='utf-8'
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                if row.get('Name') == 'Aggregated':

                    metrics = _build_locust_metrics(row)

                    break

            if not metrics:

                file.seek(0)

                reader = csv.DictReader(file)

                for row in reader:

                    metrics = _build_locust_metrics(row)

                    break

        # Cleanup generated Locust/CSV files
        for ext in [
            '_stats.csv',
            '_stats_history.csv',
            '_failures.csv',
            '_exceptions.csv'
        ]:

            try:
                os.remove(
                    f"{csv_prefix}{ext}"
                )
            except OSError:
                pass

        try:
            os.remove(locust_file)
        except OSError:
            pass

        return {
            "success": True,
            "metrics": metrics,
            "sample_output": sample_output,
            "failure_details": failure_details
        }, 200

    return {
        "success": False,
        "error": "Locust failed to generate CSV results."
    }, 500
