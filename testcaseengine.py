import json
import re

def get_required_status(config):
    req = config.get('required', False)
    if isinstance(req, str):
        return req.lower() == 'required'
    return bool(req)

def cast_value(value, target_type):
    if value is None:
        return None
    try:
        if target_type == 'integer':
            return int(value)
        if target_type == 'number':
            return float(value)
        if target_type == 'boolean':
            if isinstance(value, str):
                return value.lower() in ['true', '1', 'yes']
            return bool(value)
        if target_type == 'string':
            return str(value)
    except:
        pass
    return value

def _field_leaf_name(field_path):
    parts = [part for part in str(field_path).split('.') if not part.isdigit()]
    return parts[-1].lower() if parts else str(field_path).lower()

def _is_optional_identity_field(field_path, is_required, method_name):
    return (
        method_name == "POST" and
        not is_required and
        _field_leaf_name(field_path) in {'id', 'uuid'}
    )

def _path_prefixes(field_path):
    parts = str(field_path).split('.')
    return ['.'.join(parts[:index]) for index in range(1, len(parts))]

def _has_optional_ancestor(field_path, field_configs):
    for prefix in _path_prefixes(field_path):
        config = field_configs.get(prefix)
        if isinstance(config, dict) and not get_required_status(config):
            return True
    return False

def _parse_test_input(input_value):
    if isinstance(input_value, dict):
        return input_value
    if isinstance(input_value, str):
        try:
            parsed = json.loads(input_value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None

def _prune_configs_for_input(field_configs, input_value):
    input_obj = _parse_test_input(input_value)
    if input_obj is None or not isinstance(field_configs, dict):
        return field_configs

    flat_input = flatten(input_obj)
    pruned = {}
    for field, config in field_configs.items():
        should_skip = False
        for prefix in _path_prefixes(field):
            parent_config = field_configs.get(prefix)
            if (
                isinstance(parent_config, dict) and
                not get_required_status(parent_config) and
                prefix not in flat_input
            ):
                should_skip = True
                break
        if not should_skip:
            pruned[field] = config
    return pruned

def _sample_value_for_config(field_name, config):
    if not isinstance(config, dict):
        return "test"
    if config.get('example') is not None:
        return config.get('example')
    if config.get('default') is not None:
        return config.get('default')
    enum_values = config.get('enum')
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]

    field_type = str(config.get('type', 'string')).lower()
    if field_type == 'email':
        return 'user@example.com'
    if field_type == 'uuid':
        return '123e4567-e89b-12d3-a456-426614174000'
    if field_type == 'date':
        return '2026-01-01'
    if field_type == 'datetime':
        return '2026-01-01T00:00:00Z'
    if field_type in ('integer', 'int32', 'int64'):
        return 1
    if field_type == 'number':
        return 1.0
    if field_type == 'boolean':
        return True
    if 'status' in str(field_name).lower():
        return 'available'
    return 'test'

def _query_payload_from_configs(field_configs):
    if not isinstance(field_configs, dict):
        return {}
    payload = {}
    for field, config in field_configs.items():
        if not isinstance(config, dict):
            continue
        if config.get('in') not in (None, '', 'query'):
            continue
        payload[field] = _sample_value_for_config(field, config)
    return payload

def _body_configs(field_configs):
    if not isinstance(field_configs, dict):
        return {}
    return {
        field: config
        for field, config in field_configs.items()
        if isinstance(config, dict) and config.get('in') in (None, '', 'body')
    }

def _path_configs(endpoint, field_configs):
    names = re.findall(r'\{([^}]+)\}', endpoint or '')
    configs = {}
    for name in names:
        config = field_configs.get(name, {}) if isinstance(field_configs, dict) else {}
        configs[name] = config if isinstance(config, dict) else {}
    return configs

def _payload_from_body_configs(field_configs):
    payload = {}
    for field, config in _body_configs(field_configs).items():
        field_type = str(config.get('type', 'string')).lower()
        if field_type == 'object':
            continue
        payload = set_field(payload, field, _sample_value_for_config(field, config))
    return payload

def _ensure_put_identity_payload(payload):
    if not isinstance(payload, dict):
        return payload
    updated = json.loads(json.dumps(payload))
    if 'id' in updated and updated.get('id') in (None, '', 0, '0'):
        updated['id'] = 10
    if 'id' not in updated and any(key in updated for key in ('name', 'photoUrls', 'status', 'category', 'tags')):
        updated['id'] = 10
    if (
        'id' in updated and
        isinstance(updated.get('id'), int) and
        {'name', 'photoUrls'}.issubset(set(updated.keys()))
    ):
        updated['id'] = 10
    return updated

def _invalid_enum_value(enum_values):
    candidates = [
        "invalid_category",
        "INVALID_ENUM_VALUE",
        "__not_allowed__",
        999999,
        False
    ]
    for candidate in candidates:
        if candidate not in enum_values:
            return candidate
    return "__not_allowed__"

def _yaml_negative_expected(openapi_responses, fallback="400 Bad Request"):
    if not isinstance(openapi_responses, dict):
        return fallback

    preferred_codes = ['400', '422', '405', '409', '404']
    response_keys = {str(key): value for key, value in openapi_responses.items()}
    selected_code = next((code for code in preferred_codes if code in response_keys), None)

    if selected_code is None:
        selected_code = next(
            (
                code for code in response_keys
                if re.match(r'^4\d{2}$', code) and code not in ('401', '403')
            ),
            None
        )

    if selected_code is None:
        return fallback

    response = response_keys.get(selected_code)
    description = ''
    if isinstance(response, dict):
        description = str(response.get('description') or '').strip()
    if description:
        return f"{selected_code} {description}"
    return selected_code

def _success_expected(method_name):
    return "200 OK / 201 Created" if str(method_name).upper() == "POST" else "200 OK"

def _array_items_allow_null(config):
    if not isinstance(config, dict):
        return False

    items = config.get('items')
    if not isinstance(items, dict):
        return False

    item_type = items.get('type')
    return (
        items.get('nullable') is True or
        item_type == 'null' or
        (isinstance(item_type, list) and 'null' in item_type) or
        (isinstance(items.get('enum'), list) and None in items.get('enum'))
    )

def _is_positive_case(test_case):
    return 'positive' in str(test_case.get('type', '')).lower()

def _normalize_success_expected_statuses(test_cases, method_name):
    if str(method_name).upper() != "POST":
        return test_cases

    for test_case in test_cases:
        if _is_positive_case(test_case):
            test_case['expected'] = _success_expected(method_name)
    return test_cases

def _header_case(case_id, case_type, scenario, input_value, expected, request_headers=None, omit_headers=None):
    test_case = {
        "id": case_id,
        "type": case_type,
        "scenario": scenario,
        "input": input_value,
        "expected": expected
    }
    if request_headers:
        test_case["request_headers"] = request_headers
    if omit_headers:
        test_case["omit_headers"] = omit_headers
    return test_case

def _sample_path_value(name, config, variant='valid'):
    config = config if isinstance(config, dict) else {}
    param_type = str(config.get('type', 'string')).lower()
    if variant == 'invalid':
        return 'abc' if param_type in ('integer', 'int32', 'int64', 'number') else '@#$%'
    if variant == 'missing':
        return ''
    if variant == 'negative':
        return '-1'
    if variant == 'large':
        return '99999999999999999999'
    if variant == 'sql':
        return "123' OR '1'='1"
    if variant == 'xss':
        return '<script>alert(1)</script>'
    if variant == 'not_found':
        return '999999'
    if config.get('example') is not None:
        return str(config.get('example'))
    if config.get('default') is not None:
        return str(config.get('default'))
    if param_type in ('integer', 'int32', 'int64', 'number'):
        return '123'
    if param_type == 'uuid':
        return '123e4567-e89b-12d3-a456-426614174000'
    return 'sample'

def _endpoint_with_path_variant(endpoint, path_configs, variant='valid'):
    result = endpoint.rstrip('/')
    for name, config in path_configs.items():
        value = _sample_path_value(name, config, variant)
        if value == '':
            result = re.sub(rf'/\{{{re.escape(name)}\}}', '', result)
            result = result.replace(f'{{{name}}}', '')
        else:
            result = result.replace(f'{{{name}}}', value)
    return result or '/'

class GenerateTestcases:
    def __init__(self):
        self.test_cases = []
    
    def get_test_cases(self):
        return self.test_cases
    
    def generate_test_cases(self, data):
        method = data.get('method', 'GET')
        endpoint = data.get('endpoint', '/api/test')
        payload_json = data.get('payload', '{}')
        field_name = data.get('field_name', '').strip()
        search_string = data.get('search_string', '')
        field_configs = data.get('field_configs', {})
        openapi_responses = data.get('openapi_responses', {})
        base_url = data.get('baseUrl') or data.get('base_url') or ""
        param_type = data.get('param_type', 'query')
        
        # Debug only.
        # print(f"Base URL received for generation: {base_url}")
        
        if search_string and method.upper() in ['GET', 'DELETE']:
            self.test_cases = self._generate_query_param_testcases(
                endpoint,
                search_string,
                field_name,
                method,
                param_type,
                field_configs
            )
        else:
            self.test_cases = self._generate_testcases_internal(method, endpoint, payload_json, field_configs, openapi_responses)

        self.test_cases = _normalize_success_expected_statuses(self.test_cases, method)

        # Ensure each test case has the baseUrl, endpoint, and method for persistence
        for tc in self.test_cases:
            tc['baseUrl'] = base_url
            tc['endpoint'] = tc.get('endpoint') or endpoint
            if 'method' not in tc:
                tc['method'] = method
            tc['field_configs'] = json.loads(json.dumps(
                _prune_configs_for_input(field_configs, tc.get('input'))
            ))
        return self.test_cases
    
    def _generate_testcases_internal(self, method, endpoint, payload_json, field_configs={}, openapi_responses={}):
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except:
            payload = {}

        testcases = []
        counter = {"id": 1}
        
        if method.upper() == 'DELETE':
            testcases = self._generate_delete_testcases(endpoint, testcases, counter)
        elif method.upper() == 'POST':
            testcases = self._generate_post_testcases(endpoint, testcases, counter, payload, field_configs, "POST", openapi_responses)
        elif method.upper() == 'PUT':
            testcases = self._generate_put_testcases(endpoint, testcases, counter, payload, field_configs, openapi_responses)
        elif method.upper() == 'PATCH':
            testcases = self._generate_patch_testcases(endpoint, testcases, counter, payload, field_configs)
        elif method.upper() == 'GET':
            testcases = self._generate_get_testcases(endpoint, testcases, counter, payload, field_configs)
        else:
            testcases = self._generate_default_testcases(method, payload)
        
        if payload and method.upper() not in ['POST', 'PUT', 'PATCH']:
            testcases.extend(self._generate_payload_specific_tests(payload, method.upper(), field_configs))
        
        return testcases
    
    def _generate_payload_specific_tests(self, payload, method, field_configs={}):
        tests = []
        counter = {"id": 100}
        
        flattened = flatten(payload)
        for field, value in flattened.items():
            config = field_configs.get(field, {})
            field_type = config.get('type')
            required = get_required_status(config)
            
            if not field_type:
                field_type = detect_field_type(field, value)
            else:
                # Cast value to the type selected by user for realism
                value = cast_value(value, field_type)
            
            field_tests = generate_field_specific_tests(field, field_type, value, counter, method, required, config)
            for test in field_tests:
                if isinstance(test.get('input'), dict):
                    # Merge field-specific input into full payload
                    test_input = test['input']
                    new_input = json.loads(json.dumps(payload))
                    for k, v in test_input.items():
                        new_input = set_field(new_input, k, v)
                    test['input'] = json.dumps(new_input)
                tests.append(test)
        
        return tests
    
    def _generate_delete_testcases(self, endpoint, testcases, counter):
        # 1. Positive Tests (1 scenario)
        testcases.append({
            "id": "DELETE_POS_01",
            "type": "Positive",
            "scenario": "Valid DELETE request with valid resource ID",
            "input": f"DELETE {endpoint}/123",
            "expected": "204 No Content / 200 OK"
        })

        # 2. ID Validation - Negative Tests (9 scenarios)
        testcases.append({
            "id": "DELETE_NEG_01",
            "type": "Negative",
            "scenario": "Missing resource ID (invalid URL)",
            "input": f"DELETE {endpoint}",
            "expected": "400 Bad Request / 404 Not Found"
        })
        testcases.append({
            "id": "DELETE_NEG_02",
            "type": "Negative",
            "scenario": "Empty/null resource ID",
            "input": f"DELETE {endpoint}/null",
            "expected": "400 Bad Request / 404 Not Found"
        })
        testcases.append({
            "id": "DELETE_NEG_03",
            "type": "Negative",
            "scenario": "Invalid/malformed resource ID format",
            "input": f"DELETE {endpoint}/invalid-id-format",
            "expected": "400 Bad Request"
        })
        testcases.append({
            "id": "DELETE_NEG_04",
            "type": "Negative",
            "scenario": "Non-existent resource ID",
            "input": f"DELETE {endpoint}/999999",
            "expected": "404 Not Found"
        })
        testcases.append({
            "id": "DELETE_NEG_05",
            "type": "Negative",
            "scenario": "Negative resource ID",
            "input": f"DELETE {endpoint}/-1",
            "expected": "400 Bad Request / 404 Not Found"
        })
        testcases.append({
            "id": "DELETE_NEG_06",
            "type": "Negative",
            "scenario": "Extremely large resource ID (boundary)",
            "input": f"DELETE {endpoint}/99999999999999999999",
            "expected": "400 Bad Request / 422 Unprocessable Entity"
        })
        testcases.append({
            "id": "DELETE_NEG_07",
            "type": "Negative",
            "scenario": "Special characters in resource ID",
            "input": f"DELETE {endpoint}/@#$%^&*",
            "expected": "400 Bad Request"
        })
        testcases.append({
            "id": "DELETE_NEG_08",
            "type": "Negative/Security",
            "scenario": "SQL injection attempt in ID",
            "input": f"DELETE {endpoint}/123' OR '1'='1",
            "expected": "400 Bad Request / 403 Forbidden"
        })
        testcases.append({
            "id": "DELETE_NEG_09",
            "type": "Negative/Security",
            "scenario": "XSS attempt in ID",
            "input": f"DELETE {endpoint}/<script>alert(1)</script>",
            "expected": "400 Bad Request / 403 Forbidden"
        })

        # 3. Constraints & Dependencies (2 scenarios)
        testcases.append({
            "id": "DELETE_CON_01",
            "type": "Negative",
            "scenario": "DELETE resource with existing dependencies/references",
            "input": f"DELETE {endpoint}/123 (Resource has foreign key dependencies)",
            "expected": "409 Conflict / 422 Unprocessable Entity"
        })
        testcases.append({
            "id": "DELETE_CON_02",
            "type": "Negative",
            "scenario": "DELETE protected/system resource",
            "input": f"DELETE {endpoint}/system-config-001",
            "expected": "403 Forbidden / 409 Conflict"
        })

        # 4. State Management & Idempotency (4 scenarios)
        testcases.append({
            "id": "DELETE_STATE_01",
            "type": "Negative",
            "scenario": "DELETE resource in locked/inactive state",
            "input": f"DELETE {endpoint}/123 (Status: Locked)",
            "expected": "423 Locked / 409 Conflict"
        })
        testcases.append({
            "id": "DELETE_STATE_02",
            "type": "Positive/Idempotency",
            "scenario": "DELETE already deleted resource (idempotency test)",
            "input": f"DELETE {endpoint}/123 (Already deleted)",
            "expected": "404 Not Found / 204 No Content (idempotent)"
        })
        testcases.append({
            "id": "DELETE_STATE_03",
            "type": "Positive/Idempotency",
            "scenario": "Double DELETE - second request for same resource",
            "input": f"Two consecutive DELETE {endpoint}/123 requests",
            "expected": "404 Not Found / 204 No Content (idempotent)"
        })
        testcases.append({
            "id": "DELETE_STATE_04",
            "type": "Positive/Idempotency",
            "scenario": "Verify DELETE is idempotent (multiple same requests)",
            "input": f"Multiple identical DELETE {endpoint}/123 requests",
            "expected": "All requests succeed identically (204 or 404)"
        })

        # 5. Authentication & Authorization (9 scenarios)
        testcases.append({
            "id": "DELETE_AUTH_01",
            "type": "Security",
            "scenario": "Missing Authorization Token",
            "input": "DELETE with no Authorization header",
            "expected": "401 Unauthorized"
        })
        testcases.append({
            "id": "DELETE_AUTH_02",
            "type": "Security",
            "scenario": "Invalid/Expired Token",
            "input": "DELETE with expired JWT token",
            "expected": "401 Unauthorized"
        })
        testcases.append({
            "id": "DELETE_AUTH_03",
            "type": "Security",
            "scenario": "Malformed Authorization header",
            "input": "Authorization: Bearer malformed-token-string",
            "expected": "401 Unauthorized"
        })
        testcases.append({
            "id": "DELETE_AUTH_04",
            "type": "Security",
            "scenario": "Insufficient permissions (no delete permission)",
            "input": "DELETE with user having 'Read-Only' role",
            "expected": "403 Forbidden"
        })
        testcases.append({
            "id": "DELETE_AUTH_05",
            "type": "Security",
            "scenario": "Attempting to delete resource owned by another user",
            "input": "DELETE {endpoint}/other-user-resource-id",
            "expected": "403 Forbidden / 404 Not Found"
        })
        testcases.append({
            "id": "DELETE_AUTH_06",
            "type": "Security",
            "scenario": "CORS violation (cross-origin DELETE)",
            "input": "DELETE request from unauthorized origin",
            "expected": "403 Forbidden"
        })
        testcases.append({
            "id": "DELETE_AUTH_07",
            "type": "Security/Stability",
            "scenario": "Rate limiting exceeded",
            "input": "100+ DELETE requests in 1 second",
            "expected": "429 Too Many Requests"
        })
        testcases.append({
            "id": "DELETE_AUTH_08",
            "type": "Security",
            "scenario": "IP whitelisting violation",
            "input": "DELETE request from unauthorized IP address",
            "expected": "403 Forbidden"
        })
        testcases.append({
            "id": "DELETE_AUTH_09",
            "type": "Security",
            "scenario": "Invalid API key/client credentials",
            "input": "X-API-Key: invalid-key",
            "expected": "401 Unauthorized / 403 Forbidden"
        })

        # 6. Performance & Timeouts (6 scenarios)
        testcases.append({
            "id": "DELETE_PERF_01",
            "type": "Performance",
            "scenario": "Single DELETE response time < 200ms",
            "input": f"DELETE {endpoint}/123",
            "expected": "Response time within SLA (< 200ms)"
        })
        testcases.append({
            "id": "DELETE_PERF_02",
            "type": "Performance",
            "scenario": "Concurrent DELETE requests (10 simultaneous)",
            "input": "10 simultaneous DELETE requests",
            "expected": "All complete successfully within 2 seconds"
        })
        testcases.append({
            "id": "DELETE_PERF_03",
            "type": "Performance",
            "scenario": "Load test (50-100 DELETE requests/sec)",
            "input": "Sustained DELETE load",
            "expected": "Response time < 500ms, no 5xx errors"
        })
        testcases.append({
            "id": "DELETE_PERF_04",
            "type": "Performance",
            "scenario": "Cascading DELETE performance",
            "input": f"DELETE {endpoint}/parent-id (Has 100+ children)",
            "expected": "Completes within acceptable SLA"
        })
        testcases.append({
            "id": "DELETE_PERF_05",
            "type": "Performance/Stability",
            "scenario": "DELETE timeout - cascading deletes take too long",
            "input": "DELETE on extremely large dependency tree",
            "expected": "504 Gateway Timeout (if exceeds server limit)"
        })
        testcases.append({
            "id": "DELETE_PERF_06",
            "type": "Performance/Stability",
            "scenario": "Connection timeout during DELETE operation",
            "input": "Simulate network drop during DELETE",
            "expected": "Resource state remains consistent"
        })

        # 7. Headers & Body Validation (4 scenarios)
        testcases.append({
            "id": "DELETE_HEAD_01",
            "type": "Negative",
            "scenario": "DELETE with invalid Content-Type header",
            "input": "Content-Type: application/xml (expects json)",
            "expected": "415 Unsupported Media Type"
        })
        testcases.append({
            "id": "DELETE_HEAD_02",
            "type": "Negative",
            "scenario": "DELETE with extra request body",
            "input": 'DELETE with body {"id": 123}',
            "expected": "400 Bad Request / 204 (ignores body)"
        })
        testcases.append({
            "id": "DELETE_HEAD_03",
            "type": "Negative",
            "scenario": "DELETE multiple resources in single request",
            "input": f"DELETE {endpoint}/1,2,3",
            "expected": "400 Bad Request / Not allowed"
        })
        testcases.append({
            "id": "DELETE_HEAD_04",
            "type": "Negative",
            "scenario": "DELETE with query parameters",
            "input": f"DELETE {endpoint}?id=123",
            "expected": "400 Bad Request (unintended bulk delete)"
        })

        # 8. Advanced Scenarios (3 scenarios)
        testcases.append({
            "id": "DELETE_ADV_01",
            "type": "Advanced/Logic",
            "scenario": "Soft DELETE - resource marked as deleted but data remains",
            "input": f"DELETE {endpoint}/123",
            "expected": "204 No Content / 200 OK (IsDeleted flag set to true)"
        })
        testcases.append({
            "id": "DELETE_ADV_02",
            "type": "Advanced/Security",
            "scenario": "DELETE with audit trail - verify deletion is logged",
            "input": f"DELETE {endpoint}/123",
            "expected": "204 / 200 + audit log entry created in audit table"
        })
        testcases.append({
            "id": "DELETE_ADV_03",
            "type": "Advanced/Logic",
            "scenario": "DELETE with transaction rollback on dependency error",
            "input": "DELETE with simulated database error halfway",
            "expected": "409 Conflict / Transaction rolled back"
        })

        return testcases
    
    def _generate_payload_based_testcases(self, endpoint, testcases, counter, payload, field_configs={}, method_name="POST", openapi_responses={}):
        tests = []
        test_counter = {"id": 1}
        
        verb = "Create" if method_name == "POST" else "Update"
        success_code = _success_expected(method_name)
        negative_expected = _yaml_negative_expected(openapi_responses)

        if not payload:
            return [{
                "id": f"{method_name}_01",
                "type": "Positive",
                "scenario": f"{verb} record with valid payload",
                "input": "{}",
                "expected": success_code
            }]
        
        flattened = flatten(payload)
        fields_list = list(flattened.keys())
        
        tests.append({
            "id": f"{method_name}_{test_counter['id']:02d}",
            "type": "Positive",
            "scenario": f"{verb} record with all valid fields",
            "input": json.dumps(payload),
            "expected": success_code
        })
        test_counter['id'] += 1
        
        required_fields = [
            f for f in fields_list
            if (
                get_required_status(field_configs.get(f, {})) and
                not _has_optional_ancestor(f, field_configs)
            )
        ]
        identity_support_fields = [
            f for f in fields_list
            if (
                _is_optional_identity_field(
                    f,
                    get_required_status(field_configs.get(f, {})),
                    method_name
                ) and
                not _has_optional_ancestor(f, field_configs)
            )
        ]
        
        if required_fields:
            partial_payload_obj = {}
            for k in required_fields + identity_support_fields:
                val = flattened[k]
                conf = field_configs.get(k, {})
                if conf.get('type'):
                    val = cast_value(val, conf.get('type'))
                partial_payload_obj = set_field(partial_payload_obj, k, val)
            
            identity_suffix = " plus identity field" if identity_support_fields else ""
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Positive",
                "scenario": f"{verb} with required fields only{identity_suffix}",
                "input": json.dumps(partial_payload_obj),
                "expected": success_code
            })
            test_counter['id'] += 1
        
        for field, value in flattened.items():
            config = field_configs.get(field, {})
            field_type = config.get('type')
            is_required = get_required_status(config)
            
            if not field_type:
                field_type = detect_field_type(field, value)
            else:
                value = cast_value(value, field_type)
            enum_values = config.get('enum')
            
            # Special handling for PATCH: missing fields are positive (partial update)
            if method_name == "PATCH":
                tests.append({
                    "id": f"{method_name}_{test_counter['id']:02d}",
                    "type": "Positive",
                    "scenario": f"Partial update: Missing field {field}",
                    "input": json.dumps(remove_field(payload, field)),
                    "expected": "200 OK"
                })
            else:
                is_server_managed_identity = _is_optional_identity_field(field, is_required, method_name)
                if is_server_managed_identity:
                    continue
                elif is_required:
                    tests.append({
                        "id": f"{method_name}_{test_counter['id']:02d}",
                        "type": "Negative",
                        "scenario": f"Missing required field {field}",
                        "input": json.dumps(remove_field(payload, field)),
                        "expected": "400 Bad Request"
                    })
                else:
                    tests.append({
                        "id": f"{method_name}_{test_counter['id']:02d}",
                        "type": "Positive",
                        "scenario": f"Missing optional field {field}",
                        "input": json.dumps(remove_field(payload, field)),
                        "expected": success_code
                    })
            test_counter['id'] += 1
            
            # Null value
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Negative" if is_required or field_type not in ['string', 'email', 'url', 'password'] or (isinstance(enum_values, list) and enum_values) else "Positive",
                "scenario": f"Null value for {field}",
                "input": json.dumps(set_field(payload, field, None)),
                "expected": negative_expected if isinstance(enum_values, list) and enum_values else ("400 Bad Request" if is_required or field_type not in ['string', 'email', 'url', 'password'] else success_code)
            })
            test_counter['id'] += 1
            
            # Empty string
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Negative" if is_required or field_type != 'string' or (isinstance(enum_values, list) and enum_values) else "Positive",
                "scenario": f"Empty string for {field}",
                "input": json.dumps(set_field(payload, field, "")),
                "expected": negative_expected if isinstance(enum_values, list) and enum_values else ("400 Bad Request" if is_required or field_type != 'string' else success_code)
            })
            test_counter['id'] += 1
            
            # Type mismatch
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Negative",
                "scenario": f"Type mismatch for {field} (Expected {field_type}, sent object)",
                "input": json.dumps(set_field(payload, field, {"unexpected": "object"})),
                "expected": negative_expected if isinstance(enum_values, list) and enum_values else "400 Bad Request"
            })
            test_counter['id'] += 1

            if isinstance(enum_values, list) and enum_values:
                enum_cases = [
                    ("Positive", f"Allowed YAML enum value for {field}", enum_values[0], success_code),
                    ("Negative", f"Value outside YAML enum list for {field}", _invalid_enum_value(enum_values), negative_expected),
                    ("Negative", f"Whitespace only for {field}", "   ", negative_expected),
                    ("Negative", f"Numeric string for {field}", "12345", negative_expected),
                    ("Negative", f"String with special characters for {field}", "test!@#$%^&*()_+", negative_expected),
                    ("Negative", f"Very long string for {field}", "s" * 5000, negative_expected),
                    ("Security", f"SQL injection in {field}", "'; DROP TABLE; --", negative_expected),
                    ("Security", f"XSS attempt in {field}", "<script>alert(1)</script>", negative_expected)
                ]
                for case_type, scenario, enum_value, expected in enum_cases:
                    tests.append({
                        "id": f"{method_name}_{test_counter['id']:02d}",
                        "type": case_type,
                        "scenario": scenario,
                        "input": json.dumps(set_field(payload, field, enum_value)),
                        "expected": expected
                    })
                    test_counter['id'] += 1
                continue

            if field_type == 'boolean':
                tests.append({
                    "id": f"{method_name}_{test_counter['id']:02d}",
                    "type": "Negative",
                    "scenario": f"Boolean as string for {field}",
                    "input": json.dumps(set_field(payload, field, "true")),
                    "expected": "400 Bad Request"
                })
                test_counter['id'] += 1
            
            field_tests = generate_field_specific_tests(field, field_type, value, test_counter, method_name, is_required, config)
            for t in field_tests:
                if isinstance(t.get('input'), dict):
                    new_input = json.loads(json.dumps(payload))
                    for f, v in t['input'].items():
                        new_input = set_field(new_input, f, v)
                    t['input'] = json.dumps(new_input)
                tests.append(t)
            
            if field_type == 'email':
                tests.append({
                    "id": f"{method_name}_{test_counter['id']:02d}",
                    "type": "Validation",
                    "scenario": f"Duplicate email {field}",
                    "input": json.dumps(payload),
                    "expected": "409 Conflict"
                })
                test_counter['id'] += 1
            
            # Security tests
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": f"SQL injection in {field}",
                "input": json.dumps(set_field(payload, field, "'; DROP TABLE; --")),
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1
            
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": f"XSS attempt in {field}",
                "input": json.dumps(set_field(payload, field, "<script>alert(1)</script>")),
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1
            
            tests.append({
                "id": f"{method_name}_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": f"Very long value in {field}",
                "input": json.dumps(set_field(payload, field, "x" * 1000)),
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1
        
        # Payload level validations
        tests.extend([
            {"id": f"{method_name}_{test_counter['id']:02d}", "type": "Validation", "scenario": "Empty JSON payload", "input": "{}", "expected": "400 Bad Request"},
            {"id": f"{method_name}_{test_counter['id']+1:02d}", "type": "Validation", "scenario": "Malformed JSON", "input": "{invalid json}", "expected": "400 Bad Request"},
            {"id": f"{method_name}_{test_counter['id']+2:02d}", "type": "Validation", "scenario": "Extra unknown fields", "input": json.dumps({**payload, "unknown_field": "value"}), "expected": "400 Bad Request"}
        ])
        test_counter['id'] += 3
        
        # Standard Headers/Auth/RateLimit
        tests.extend([
            _header_case(f"{method_name}_{test_counter['id']:02d}", "Header", "Missing Content-Type header", json.dumps(payload), "415 Unsupported Media Type", omit_headers=["Content-Type"]),
            _header_case(f"{method_name}_{test_counter['id']+1:02d}", "Header", "Invalid Content-Type header", json.dumps(payload), "415 Unsupported Media Type", request_headers={"Content-Type": "text/plain"}),
            _header_case(f"{method_name}_{test_counter['id']+2:02d}", "Auth", "Missing authorization token", json.dumps(payload), "401 Unauthorized", omit_headers=["Authorization"]),
            _header_case(f"{method_name}_{test_counter['id']+3:02d}", "Auth", "Invalid token", json.dumps(payload), "401 Unauthorized", request_headers={"Authorization": "Bearer invalid-token"}),
            {"id": f"{method_name}_{test_counter['id']+4:02d}", "type": "RateLimit", "scenario": "Exceed rate limit", "input": json.dumps(payload), "expected": "429 Too Many Requests"},
            {"id": f"{method_name}_{test_counter['id']+5:02d}", "type": "Performance", "scenario": "Normal load response time", "input": json.dumps(payload), "expected": "<300 ms latency"},
            {"id": f"{method_name}_{test_counter['id']+6:02d}", "type": "Integration", "scenario": f"{verb} and verify record", "input": json.dumps(payload), "expected": success_code}
        ])
        test_counter['id'] += 7
        
        return tests
    
    def _generate_post_testcases(self, endpoint, testcases, counter, payload, field_configs={}, method_name="POST", openapi_responses={}):
        return self._generate_payload_based_testcases(endpoint, testcases, counter, payload, field_configs, method_name, openapi_responses)

    def _generate_put_testcases_static(self, endpoint, testcases, counter, payload=None, field_configs={}):
        payload = payload if isinstance(payload, dict) else {}
        payload_json = json.dumps(payload)
        fields = flatten(payload)
        field_names = list(fields)
        email_field = next((field for field in field_names if field.lower().endswith('email')), 'email')
        first_name_field = next((field for field in field_names if field.lower().endswith(('firstname', 'first_name'))), 'firstName')
        phone_field = next((field for field in field_names if field.lower().endswith('phone')), 'phone')
        id_value = '999999'
        valid_id = '123'
        path_endpoint = endpoint.rstrip('/')
        if '{' in path_endpoint:
            path_endpoint = re.sub(r'\{[^}]+\}', id_value, path_endpoint)
        else:
            path_endpoint = f"{path_endpoint}/{id_value}"
        valid_path_endpoint = endpoint.rstrip('/')
        if '{' in valid_path_endpoint:
            valid_path_endpoint = re.sub(r'\{[^}]+\}', valid_id, valid_path_endpoint)
        else:
            valid_path_endpoint = f"{valid_path_endpoint}/{valid_id}"
        invalid_path_endpoint = re.sub(r'\{[^}]+\}', 'abc', endpoint.rstrip('/')) if '{' in endpoint else f"{endpoint.rstrip('/')}/abc"
        negative_path_endpoint = re.sub(r'\{[^}]+\}', '-1', endpoint.rstrip('/')) if '{' in endpoint else f"{endpoint.rstrip('/')}/-1"
        missing_path_endpoint = re.sub(r'/\{[^}]+\}', '/', endpoint.rstrip('/')) if '{' in endpoint else f"{endpoint.rstrip('/')}/"

        def field_value(field, fallback='test'):
            return fields.get(field, fallback)

        def with_field(field, value):
            updated = json.loads(json.dumps(payload))
            return json.dumps(set_field(updated, field, value))

        def without_field(field):
            return json.dumps(remove_field(payload, field))

        single_field = {}
        if field_names:
            single_field = set_field({}, field_names[0], field_value(field_names[0]))

        return [
            {"id": "PUT-001", "type": "Positive", "scenario": "Valid PUT request", "input": f"PUT {valid_path_endpoint} with complete valid payload {payload_json}", "expected": "200 OK / 204 No Content; data updated"},
            {"id": "PUT-002", "type": "Positive", "scenario": "Update single field", "input": json.dumps(single_field) if single_field else payload_json, "expected": "Behavior matches API contract; verify remaining fields"},
            {"id": "PUT-003", "type": "Positive", "scenario": "Update all fields", "input": payload_json, "expected": "All fields updated correctly"},
            {"id": "PUT-004", "type": "Negative", "scenario": "Non-existing resource", "input": f"PUT {path_endpoint}", "expected": "404 Not Found"},
            {"id": "PUT-005", "type": "Negative", "scenario": "Invalid resource ID", "input": f"PUT {invalid_path_endpoint}", "expected": "400 Bad Request or contract-defined error"},
            {"id": "PUT-006", "type": "Negative", "scenario": "Negative ID", "input": f"PUT {negative_path_endpoint}", "expected": "Validation error"},
            {"id": "PUT-007", "type": "Negative", "scenario": "Missing path parameter", "input": f"PUT {missing_path_endpoint}", "expected": "400 Bad Request / 404 Not Found"},
            {"id": "PUT-008", "type": "Validation", "scenario": "Empty payload", "input": "{}", "expected": "Validation error if fields are mandatory"},
            {"id": "PUT-009", "type": "Validation", "scenario": "Null payload", "input": "null", "expected": "400 Bad Request"},
            {"id": "PUT-010", "type": "Validation", "scenario": "Missing mandatory field", "input": without_field(email_field), "expected": "Validation error"},
            {"id": "PUT-011", "type": "Validation", "scenario": "Null mandatory field", "input": with_field(email_field, None), "expected": "Validation error"},
            {"id": "PUT-012", "type": "Validation", "scenario": "Empty mandatory field", "input": with_field(first_name_field, ""), "expected": "Validation error"},
            {"id": "PUT-013", "type": "Validation", "scenario": "Invalid data type", "input": with_field(phone_field, 9876543210), "expected": "Validation error"},
            {"id": "PUT-014", "type": "Validation", "scenario": "Invalid email", "input": with_field(email_field, "abc"), "expected": "Validation error"},
            {"id": "PUT-015", "type": "Boundary", "scenario": "Boundary string length", "input": with_field(first_name_field, "x"), "expected": "Accepted/rejected according to specification"},
            {"id": "PUT-016", "type": "Boundary", "scenario": "Exceed max length", "input": with_field(first_name_field, "x" * 1000), "expected": "400 Bad Request"},
            {"id": "PUT-017", "type": "Validation", "scenario": "Special characters", "input": with_field(first_name_field, "@@@###"), "expected": "Accepted/rejected according to rules"},
            {"id": "PUT-018", "type": "Validation", "scenario": "Unicode characters", "input": with_field(first_name_field, "José"), "expected": "Correctly handled"},
            {"id": "PUT-019", "type": "Security", "scenario": "SQL injection string", "input": with_field(first_name_field, "' OR 1=1 --"), "expected": "Request rejected/safely handled"},
            {"id": "PUT-020", "type": "Security", "scenario": "XSS payload", "input": with_field(first_name_field, "<script>alert(1)</script>"), "expected": "Safely handled/rejected"},
            {"id": "PUT-021", "type": "Validation", "scenario": "Extra unknown field", "input": json.dumps({**payload, "admin": True}), "expected": "Ignored or rejected according to contract"},
            {"id": "PUT-022", "type": "Header", "scenario": "Missing Content-Type", "input": payload_json, "expected": "400 Bad Request / 415 Unsupported Media Type"},
            {"id": "PUT-023", "type": "Header", "scenario": "Wrong Content-Type", "input": "Content-Type: text/plain", "expected": "415 Unsupported Media Type"},
            {"id": "PUT-024", "type": "Validation", "scenario": "Invalid JSON", "input": "{malformed json}", "expected": "400 Bad Request"},
            {"id": "PUT-025", "type": "Security", "scenario": "Empty Authorization", "input": "PUT request with no Authorization token", "expected": "401 Unauthorized"},
            {"id": "PUT-026", "type": "Security", "scenario": "Invalid token", "input": "PUT request with random or expired token", "expected": "401 Unauthorized"},
            {"id": "PUT-027", "type": "Security", "scenario": "Insufficient permission", "input": "PUT request from read-only user", "expected": "403 Forbidden"},
            {"id": "PUT-028", "type": "Security", "scenario": "Update another user's resource", "input": f"User A token + User B ID at {path_endpoint}", "expected": "403 or contract-defined behavior"},
            {"id": "PUT-029", "type": "Validation", "scenario": "Duplicate email", "input": with_field(email_field, "existing@example.com"), "expected": "409 Conflict or validation error"},
            {"id": "PUT-030", "type": "Positive", "scenario": "Same existing values", "input": payload_json, "expected": "Successful; no unintended changes"},
            {"id": "PUT-031", "type": "Positive", "scenario": "Idempotency", "input": "Send the same PUT request twice", "expected": "Final state remains the same"},
            {"id": "PUT-032", "type": "Integration", "scenario": "Verify persistence", "input": "PUT followed by GET", "expected": "GET returns updated values"},
            {"id": "PUT-033", "type": "Integration", "scenario": "Verify database", "input": "PUT followed by database query", "expected": "Database contains expected values"},
            {"id": "PUT-034", "type": "Integration", "scenario": "Response schema", "input": payload_json, "expected": "Response JSON matches OpenAPI/schema"},
            {"id": "PUT-035", "type": "Header", "scenario": "Response headers", "input": payload_json, "expected": "Content-Type, correlation ID, and other headers match contract"},
            {"id": "PUT-036", "type": "Performance", "scenario": "Response time", "input": payload_json, "expected": "Within SLA"},
            {"id": "PUT-037", "type": "Concurrency", "scenario": "Concurrent updates", "input": "Two PUT requests simultaneously", "expected": "Defined conflict/versioning behavior"},
            {"id": "PUT-038", "type": "Concurrency", "scenario": "Stale version", "input": "Old version or ETag", "expected": "409 Conflict / 412 Precondition Failed if optimistic locking is implemented"},
            {"id": "PUT-039", "type": "Boundary", "scenario": "Large payload", "input": "Maximum allowed payload", "expected": "Correctly processed/rejected"},
            {"id": "PUT-040", "type": "Negative", "scenario": "Unsupported HTTP method", "input": f"POST/PATCH/DELETE against {endpoint}", "expected": "Correct method handling"}
        ]

    def _generate_put_testcases(self, endpoint, testcases, counter, payload=None, field_configs={}, openapi_responses={}):
        field_configs = field_configs or {}
        payload = payload if isinstance(payload, dict) else {}
        if not payload:
            payload = _payload_from_body_configs(field_configs)
        payload = _ensure_put_identity_payload(payload)

        body_configs = _body_configs(field_configs)
        fields = flatten(payload)
        field_names = [
            field for field in fields.keys()
            if field in body_configs or not body_configs
        ]
        for field, config in body_configs.items():
            if isinstance(config.get('enum'), list) and field not in field_names:
                field_names.append(field)
        path_configs = _path_configs(endpoint, field_configs)
        valid_endpoint = (
            _endpoint_with_path_variant(endpoint, path_configs, 'valid')
            if path_configs else endpoint.rstrip('/')
        )

        tests = []
        test_counter = {"id": 1}

        def next_id():
            case_id = f"PUT_{test_counter['id']:03d}"
            test_counter['id'] += 1
            return case_id

        def add_case(case_type, scenario, input_value, expected, endpoint_override=None, metadata=None):
            case = {
                "id": next_id(),
                "type": case_type,
                "scenario": scenario,
                "input": input_value,
                "expected": expected,
                "endpoint": endpoint_override or valid_endpoint,
                "method": "PUT"
            }
            if metadata:
                case["metadata"] = metadata
            tests.append(case)

        payload_json = json.dumps(payload)
        negative_expected = _yaml_negative_expected(openapi_responses)
        yaml_flow = {
            "source": "uploaded_yaml",
            "flow": [
                "Read Swagger/YAML",
                "Identify PUT endpoint",
                "Read path parameters",
                "Read request schema",
                "Execute PUT",
                "Validate status code",
                "Validate response schema",
                "GET resource",
                "Verify updated values",
                "Store result",
                "Dashboard"
            ],
            "follow_up": {
                "method": "GET",
                "endpoint": valid_endpoint,
                "assert_updated_values": payload
            }
        }

        add_case(
            "Positive",
            "Valid PUT using YAML path parameters and request schema",
            payload_json,
            "200 OK / 204 No Content; response schema is valid and resource is updated",
            metadata=yaml_flow
        )

        required_fields = [
            field for field in field_names
            if get_required_status(body_configs.get(field, {})) and not _has_optional_ancestor(field, body_configs)
        ]
        optional_fields = [
            field for field in field_names
            if field not in required_fields and not _has_optional_ancestor(field, body_configs)
        ]

        if required_fields:
            required_payload = {}
            for field in required_fields:
                required_payload = set_field(
                    required_payload,
                    field,
                    fields.get(field, _sample_value_for_config(field, body_configs.get(field, {})))
                )
            add_case(
                "Validation",
                "PUT with required YAML schema fields only",
                json.dumps(required_payload),
                "200 OK only if API allows partial replacement; otherwise 400 Bad Request"
            )

        if field_names:
            first_field = field_names[0]
            partial_payload = set_field({}, first_field, fields[first_field])
            add_case(
                "Validation",
                f"PUT with single field only {first_field}",
                json.dumps(partial_payload),
                "400 Bad Request unless API explicitly supports partial PUT"
            )

        if path_configs:
            path_cases = [
                ("Negative", "PUT with non-existing path parameter", "not_found", "404 Not Found"),
                ("Negative", "PUT with invalid path parameter format", "invalid", "400 Bad Request"),
                ("Boundary", "PUT with negative path parameter", "negative", "400 Bad Request / 404 Not Found"),
                ("Boundary", "PUT with extremely large path parameter", "large", "400 Bad Request / 422 Unprocessable Entity"),
                ("Negative", "PUT with missing required path parameter", "missing", "400 Bad Request / 404 Not Found"),
                ("Security", "PUT with SQL injection in path parameter", "sql", "400 Bad Request / 403 Forbidden"),
                ("Security", "PUT with XSS in path parameter", "xss", "400 Bad Request / 403 Forbidden")
            ]
            for case_type, scenario, variant, expected in path_cases:
                add_case(
                    case_type,
                    scenario,
                    payload_json,
                    expected,
                    endpoint_override=_endpoint_with_path_variant(endpoint, path_configs, variant)
                )

        for field in required_fields:
            add_case(
                "Negative",
                f"Missing required YAML request field {field}",
                json.dumps(remove_field(payload, field)),
                "400 Bad Request"
            )
            add_case(
                "Negative",
                f"Null required YAML request field {field}",
                json.dumps(set_field(payload, field, None)),
                "400 Bad Request"
            )

        for field in optional_fields[:3]:
            add_case(
                "Validation",
                f"PUT without optional YAML request field {field}",
                json.dumps(remove_field(payload, field)),
                "200 OK only if omission preserves/replaces field according to API contract"
            )

        for field in field_names:
            config = body_configs.get(field, {})
            value = fields.get(field, _sample_value_for_config(field, config))
            field_type = config.get('type') or detect_field_type(field, value)
            is_required = get_required_status(config)
            typed_value = cast_value(value, field_type)
            enum_values = config.get('enum')

            if isinstance(enum_values, list) and enum_values:
                add_case(
                    "Positive",
                    f"PUT with allowed YAML enum value for {field}",
                    json.dumps(set_field(payload, field, enum_values[0])),
                    "200 OK / 204 No Content"
                )
                add_case(
                    "Negative",
                    f"PUT with {field} value outside YAML enum list",
                    json.dumps(set_field(payload, field, _invalid_enum_value(enum_values))),
                    negative_expected
                )
                add_case(
                    "Negative",
                    f"PUT with empty {field} outside YAML enum list",
                    json.dumps(set_field(payload, field, "")),
                    negative_expected
                )
                add_case(
                    "Negative",
                    f"PUT with numeric string {field} outside YAML enum list",
                    json.dumps(set_field(payload, field, "12345")),
                    negative_expected
                )
                add_case(
                    "Negative",
                    f"PUT with special characters in {field} outside YAML enum list",
                    json.dumps(set_field(payload, field, "test!@#$%^&*()_+")),
                    negative_expected
                )
                add_case(
                    "Negative",
                    f"PUT with long {field} value outside YAML enum list",
                    json.dumps(set_field(payload, field, "x" * 1000)),
                    negative_expected
                )
                add_case(
                    "Negative",
                    f"Type mismatch for YAML enum field {field}",
                    json.dumps(set_field(payload, field, {"unexpected": "object"})),
                    negative_expected
                )
                add_case(
                    "Security",
                    f"SQL injection in YAML enum field {field}",
                    json.dumps(set_field(payload, field, "'; DROP TABLE users; --")),
                    negative_expected
                )
                add_case(
                    "Security",
                    f"XSS payload in YAML enum field {field}",
                    json.dumps(set_field(payload, field, "<script>alert(1)</script>")),
                    negative_expected
                )
                continue

            add_case(
                "Negative",
                f"Type mismatch for YAML request field {field}",
                json.dumps(set_field(payload, field, {"unexpected": "object"})),
                "400 Bad Request"
            )

            if field_type in ('string', 'email', 'url', 'password', 'phone', 'uuid', 'date', 'datetime'):
                add_case(
                    "Boundary" if not is_required else "Negative",
                    f"Empty string boundary for YAML request field {field}",
                    json.dumps(set_field(payload, field, "")),
                    "400 Bad Request" if is_required else "200 OK / 400 Bad Request based on YAML contract"
                )
                if config.get('maxLength'):
                    add_case(
                        "Boundary",
                        f"Max length boundary for YAML request field {field}",
                        json.dumps(set_field(payload, field, "x" * int(config.get('maxLength')))),
                        "200 OK / 204 No Content"
                    )
                    add_case(
                        "Boundary",
                        f"Exceeds max length for YAML request field {field}",
                        json.dumps(set_field(payload, field, "x" * (int(config.get('maxLength')) + 1))),
                        "400 Bad Request"
                    )
                if config.get('minLength'):
                    add_case(
                        "Boundary",
                        f"Below min length for YAML request field {field}",
                        json.dumps(set_field(payload, field, "x" * max(0, int(config.get('minLength')) - 1))),
                        "400 Bad Request"
                    )

            if field_type in ('integer', 'number', 'int32', 'int64', 'float', 'double'):
                if config.get('minimum') is not None:
                    add_case(
                        "Boundary",
                        f"Minimum numeric boundary for YAML request field {field}",
                        json.dumps(set_field(payload, field, config.get('minimum'))),
                        "200 OK / 204 No Content"
                    )
                if config.get('maximum') is not None:
                    add_case(
                        "Boundary",
                        f"Maximum numeric boundary for YAML request field {field}",
                        json.dumps(set_field(payload, field, config.get('maximum'))),
                        "200 OK / 204 No Content"
                    )
                add_case(
                    "Negative",
                    f"Out-of-range numeric value for YAML request field {field}",
                    json.dumps(set_field(payload, field, 999999999999999999)),
                    "400 Bad Request / 422 Unprocessable Entity"
                )

            field_tests = generate_field_specific_tests(field, field_type, typed_value, test_counter, "PUT", is_required, config)
            for field_test in field_tests:
                field_input = field_test.get('input')
                if isinstance(field_input, dict):
                    updated = json.loads(json.dumps(payload))
                    for input_field, input_value in field_input.items():
                        updated = set_field(updated, input_field, input_value)
                    field_test['input'] = json.dumps(updated)
                field_test['endpoint'] = valid_endpoint
                field_test['method'] = "PUT"
                tests.append(field_test)

            add_case(
                "Security",
                f"SQL injection in YAML request field {field}",
                json.dumps(set_field(payload, field, "'; DROP TABLE users; --")),
                "400 Bad Request / 403 Forbidden"
            )
            add_case(
                "Security",
                f"XSS payload in YAML request field {field}",
                json.dumps(set_field(payload, field, "<script>alert(1)</script>")),
                "400 Bad Request / 403 Forbidden"
            )

        add_case("Negative", "Empty JSON body for PUT", "{}", "400 Bad Request")
        add_case("Negative", "Malformed JSON body for PUT", "{invalid json}", "400 Bad Request")
        add_case("Validation", "PUT with extra field not defined in YAML schema", json.dumps({**payload, "unknown_field": "unexpected"}), "400 Bad Request / ignored according to API contract")
        add_case("Header", "PUT missing Content-Type header", payload_json, "415 Unsupported Media Type", metadata={"omit_headers": ["Content-Type"]})
        add_case("Header", "PUT with invalid Content-Type header", payload_json, "415 Unsupported Media Type", metadata={"request_headers": {"Content-Type": "text/plain"}})
        add_case("Security", "PUT missing authorization token", payload_json, "401 Unauthorized", metadata={"omit_headers": ["Authorization"]})
        add_case("Security", "PUT with invalid authorization token", payload_json, "401 Unauthorized", metadata={"request_headers": {"Authorization": "Bearer invalid-token"}})
        add_case("Security", "PUT with insufficient update permission", payload_json, "403 Forbidden")
        add_case("RateLimit", "PUT rate limit exceeded", payload_json, "429 Too Many Requests")
        add_case("Integration", "Validate PUT response schema from uploaded YAML", payload_json, "Response body matches YAML/OpenAPI response schema", metadata=yaml_flow)
        add_case("Integration", "GET resource after PUT and verify updated values", payload_json, "GET returns updated values from PUT payload", metadata=yaml_flow)
        add_case("Positive", "PUT idempotency with same YAML payload repeated", payload_json, "Final resource state remains unchanged after repeated PUT", metadata={**yaml_flow, "repeat_request": 2})
        add_case("Performance", "PUT response time within SLA", payload_json, "Response time within configured SLA")

        if not tests:
            return self._generate_put_testcases_static(endpoint, testcases, counter, payload, field_configs)

        for tc in tests:
            tc.setdefault("field_configs", field_configs)

        return tests

    def _generate_patch_testcases(self, endpoint, testcases, counter, payload=None, field_configs={}):
        if payload:
            return self._generate_payload_based_testcases(endpoint, testcases, counter, payload, field_configs, "PATCH")
        return [{"id": "PATCH_01", "type": "Positive", "scenario": "Partial update", "input": "Valid JSON", "expected": "200 OK"}]
    
    def _generate_get_testcases(self, endpoint, testcases, counter, payload, field_configs={}):
        tests = []
        test_counter = {"id": 1}
        
        # Determine if there are path parameters
        path_params = re.findall(r"\{(\w+)\}", endpoint)
        
        # 1. Common GET API Test Cases
        self._add_common_get_tests(tests, test_counter)
        
        # 2. GET WITHOUT Parameters
        if not payload and not path_params:
            self._add_get_no_params_tests(tests, test_counter, endpoint)
        
        # 3. GET WITH Path Parameters
        if path_params:
            self._add_get_path_params_tests(tests, test_counter, endpoint, path_params)
            
        query_payload = payload or _query_payload_from_configs(field_configs)

        # 4. GET WITH Query Parameters
        if query_payload:
            self._add_get_query_params_tests(tests, test_counter, query_payload, field_configs)
            
        # 5. Authorization & Access Control
        self._add_auth_access_tests(tests, test_counter)
        
        # 6. Performance & Reliability
        self._add_performance_reliability_tests(tests, test_counter)
        
        # 7. Error Handling
        self._add_error_handling_tests(tests, test_counter)
        
        # 8. Compatibility
        self._add_compatibility_tests(tests, test_counter)

        for tc in tests:
            tc["field_configs"] = field_configs
        
        return tests

    def _add_common_get_tests(self, tests, test_counter):
        # Functional
        common_functional = [
            ("Verify API returns 200 OK for valid request", "Valid request", "200 OK"),
            ("Verify response body is not empty", "Valid request", "Response body contains data"),
            ("Verify response matches contract/schema", "Valid request", "Schema validation passes"),
            ("Verify correct Content-Type (application/json)", "Valid request", "Content-Type: application/json"),
            ("Verify response time is within SLA", "Valid request", "Response time < 500ms"),
            ("Verify correct character encoding", "Valid request", "UTF-8 encoding"),
            ("Verify response does not modify server state", "Valid request", "No data change on server")
        ]
        for scenario, inp, exp in common_functional:
            tests.append({
                "id": f"GET_COM_FUN_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # Security
        common_security = [
            ("Verify API rejects request without authentication", "Missing Auth header", "401 Unauthorized"),
            ("Verify API rejects request with invalid token", "Invalid token", "401 Unauthorized"),
            ("Verify API does not expose sensitive fields", "Valid request", "No password/SSN in response"),
            ("Verify API is protected against IDOR", "Request other user's resource", "403 Forbidden / 404 Not Found"),
            ("Verify HTTPS is enforced", "HTTP request", "301 Redirect to HTTPS / 403 Forbidden")
        ]
        for scenario, inp, exp in common_security:
            tests.append({
                "id": f"GET_COM_SEC_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # Headers
        common_headers = [
            ("Verify required headers are mandatory", "Missing required header (e.g. X-API-KEY)", "400 Bad Request / 401 Unauthorized"),
            ("Verify unsupported headers are ignored", "Send extra header 'X-Custom: value'", "200 OK, header ignored"),
            ("Verify correct CORS headers", "OPTIONS request / Origin header", "Access-Control-Allow-Origin present")
        ]
        for scenario, inp, exp in common_headers:
            tests.append({
                "id": f"GET_COM_HDR_{test_counter['id']:02d}",
                "type": "Header",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

    def _add_get_no_params_tests(self, tests, test_counter, endpoint):
        no_params_cases = [
            ("Positive", "Verify all records are returned", f"GET {endpoint}", "200 OK, full list returned"),
            ("Positive", "Verify pagination defaults are applied", f"GET {endpoint}", "200 OK, default page 1, size 10"),
            ("Positive", "Verify response ordering (default sort)", f"GET {endpoint}", "200 OK, sorted by created date"),
            ("Positive", "Verify empty dataset returns valid response", f"GET {endpoint} (no data)", "200 OK, empty list []"),
            ("Negative", "Verify unsupported HTTP method returns 405", f"POST {endpoint}", "405 Method Not Allowed"),
            ("Negative", "Verify invalid endpoint returns 404", f"GET {endpoint}/invalid", "404 Not Found"),
            ("Negative", "Verify server handles large data safely", f"GET {endpoint} (large dataset)", "200 OK, handled gracefully / 503 if timeout")
        ]
        for t_type, scenario, inp, exp in no_params_cases:
            tests.append({
                "id": f"GET_NOPARAM_{test_counter['id']:02d}",
                "type": t_type,
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

    def _add_get_path_params_tests(self, tests, test_counter, endpoint, path_params):
        for param in path_params:
            # Positive
            pos_cases = [
                (f"Verify valid {param} returns correct record", f"{param}=123", "200 OK, correct resource"),
                (f"Verify numeric ID works correctly for {param}", f"{param}=456", "200 OK"),
                (f"Verify alphanumeric ID works for {param} (if allowed)", f"{param}=ABC-789", "200 OK"),
                (f"Verify response contains correct resource ID for {param}", f"{param}=123", f"Response contains {param}=123")
            ]
            for scenario, inp, exp in pos_cases:
                tests.append({
                    "id": f"GET_PATH_POS_{test_counter['id']:02d}",
                    "type": "Positive",
                    "scenario": scenario,
                    "input": inp,
                    "expected": exp
                })
                test_counter["id"] += 1

            # Boundary
            boundary_cases = [
                (f"Verify minimum {param} value", f"{param}=1", "200 OK"),
                (f"Verify maximum {param} value", f"{param}=9223372036854775807", "200 OK / 400 if too large"),
                (f"Verify leading zeros in {param}", f"{param}=00123", "200 OK (id=123)"),
                (f"Verify very large {param} value", f"{param}=999999999999999", "200 OK / 400 Out of range")
            ]
            for scenario, inp, exp in boundary_cases:
                tests.append({
                    "id": f"GET_PATH_BND_{test_counter['id']:02d}",
                    "type": "Boundary",
                    "scenario": scenario,
                    "input": inp,
                    "expected": exp
                })
                test_counter["id"] += 1

            # Negative
            neg_cases = [
                (f"Verify invalid {param} returns 400", f"{param}=invalid_id", "400 Bad Request"),
                (f"Verify non-existing {param} returns 404", f"{param}=999999", "404 Not Found"),
                (f"Verify special characters in {param} return 400", f"{param}=@#$%", "400 Bad Request"),
                (f"Verify null/empty {param} handling", f"{param}=", "400 Bad Request / 404 Not Found"),
                (f"Verify SQL injection in {param}", f"{param}=1' OR '1'='1", "400 Bad Request"),
                (f"Verify script injection in {param}", f"{param}=<script>alert(1)</script>", "400 Bad Request")
            ]
            for scenario, inp, exp in neg_cases:
                tests.append({
                    "id": f"GET_PATH_NEG_{test_counter['id']:02d}",
                    "type": "Negative",
                    "scenario": scenario,
                    "input": inp,
                    "expected": exp
                })
                test_counter["id"] += 1

    def _add_get_query_params_tests(self, tests, test_counter, payload, field_configs={}):
        flattened = flatten(payload)
        fields_list = list(flattened.keys())
        required_fields = [
            field for field in fields_list
            if get_required_status(field_configs.get(field, {}))
        ]
        optional_fields = [
            field for field in fields_list
            if not get_required_status(field_configs.get(field, {}))
        ]
        
        # Functional
        tests.append({
            "id": f"GET_QUERY_FUN_{test_counter['id']:02d}",
            "type": "Functional",
            "scenario": "Verify valid query parameters filter results correctly",
            "input": "&".join([f"{f}={flattened[f]}" for f in fields_list]),
            "expected": "200 OK, filtered results"
        })
        test_counter['id'] += 1
        
        if len(fields_list) > 1:
            tests.append({
                "id": f"GET_QUERY_FUN_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": "Verify multiple query parameters work together",
                "input": "&".join([f"{fields_list[0]}={flattened[fields_list[0]]}", f"{fields_list[1]}={flattened[fields_list[1]]}"]),
                "expected": "200 OK, results match both criteria"
            })
            test_counter['id'] += 1

        if required_fields:
            required_field = required_fields[0]
            included_fields = [
                field for field in fields_list
                if field != required_field
            ]
            missing_required_input = "&".join(
                [f"{field}={flattened[field]}" for field in included_fields]
            )
            tests.append({
                "id": f"GET_QUERY_NEG_{test_counter['id']:02d}",
                "type": "Negative",
                "scenario": f"Missing required query parameter {required_field}",
                "input": missing_required_input or "Omit required query parameter",
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1

        if optional_fields:
            optional_field = optional_fields[0]
            included_fields = [
                field for field in fields_list
                if field != optional_field
            ]
            optional_input = "&".join(
                [f"{field}={flattened[field]}" for field in included_fields]
            )
            tests.append({
                "id": f"GET_QUERY_FUN_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": f"Verify optional query parameter {optional_field} is truly optional",
                "input": optional_input or "Omit optional query parameter",
                "expected": "200 OK, results returned ignoring omitted fields"
            })
            test_counter['id'] += 1

        # Pagination
        pagination_cases = [
            ("Verify page number works correctly", "page=2", "200 OK, returns second page"),
            ("Verify page size limit is enforced", "size=1000", "200 OK, capped at max size (e.g. 100)"),
            ("Verify page beyond max returns empty list", "page=999999", "200 OK, empty list []"),
            ("Verify total count value is correct", "page=1&size=10", "Response contains totalCount field")
        ]
        for scenario, inp, exp in pagination_cases:
            tests.append({
                "id": f"GET_QUERY_PAG_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # Sorting
        sorting_cases = [
            ("Verify ascending sort works", "sort=name,asc", "200 OK, sorted A-Z"),
            ("Verify descending sort works", "sort=name,desc", "200 OK, sorted Z-A"),
            ("Verify invalid sort field returns error or default", "sort=invalid_field", "400 Bad Request or 200 with default sort"),
            ("Verify multi-field sorting", "sort=status,asc&sort=createdAt,desc", "200 OK, multi-level sort applied")
        ]
        for scenario, inp, exp in sorting_cases:
            tests.append({
                "id": f"GET_QUERY_SRT_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # Filtering specific fields
        for field, value in flattened.items():
            field_type = detect_field_type(field, value)
            
            # Basic Filtering
            tests.append({
                "id": f"GET_QUERY_FLT_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": f"Verify single filter criteria for {field}",
                "input": f"{field}={value}",
                "expected": "200 OK, filtered results"
            })
            test_counter['id'] += 1
            
            # Negative / Edge for query params
            tests.append({
                "id": f"GET_QUERY_NEG_{test_counter['id']:02d}",
                "type": "Negative",
                "scenario": f"Verify invalid query param value for {field}",
                "input": f"{field}=invalid_val_@#$%",
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1

            # Security for query params
            tests.append({
                "id": f"GET_QUERY_SEC_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": f"SQL injection in query parameter {field}",
                "input": f"{field}=1' OR '1'='1",
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1

            tests.append({
                "id": f"GET_QUERY_SEC_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": f"XSS injection in query parameter {field}",
                "input": f"{field}=<script>alert(1)</script>",
                "expected": "400 Bad Request"
            })
            test_counter['id'] += 1

    def _add_auth_access_tests(self, tests, test_counter):
        auth_cases = [
            ("Verify user can access own data", "Auth: User A, Resource: User A data", "200 OK"),
            ("Verify user cannot access others' data", "Auth: User A, Resource: User B data", "403 Forbidden / 404 Not Found"),
            ("Verify role-based access control", "Auth: Regular User, Resource: Admin only", "403 Forbidden / 404 Not Found"),
            ("Verify expired token handling", "Auth: Expired JWT", "401 Unauthorized"),
            ("Verify revoked token handling", "Auth: Revoked/Blacklisted token", "401 Unauthorized")
        ]
        for scenario, inp, exp in auth_cases:
            tests.append({
                "id": f"GET_AUTH_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

    def _add_performance_reliability_tests(self, tests, test_counter):
        perf_cases = [
            (
                "GET_PERF_RESPONSE_TIME",
                "Verify response time under load",
                "100 concurrent users",
                "Average response time < 1s"
            ),
            (
                "GET_PERF_CONCURRENT",
                "Verify API supports concurrent requests",
                "Multiple simultaneous GETs",
                "No deadlocks, all requests succeed"
            ),
            (
                "GET_PERF_RATE_LIMIT",
                "Verify rate limiting behavior",
                ">100 requests per minute",
                "429 Too Many Requests"
            ),
            (
                "GET_PERF_CACHING",
                "Verify caching headers (ETag / Cache-Control)",
                "Repeated request",
                "200 OK with Cache-Control / 304 Not Modified"
            ),
            (
                "GET_PERF_CONDITIONAL",
                "Verify conditional GET (If-None-Match)",
                "Request with If-None-Match ETag",
                "304 Not Modified if content hasn't changed"
            )
        ]
        for scenario_id, scenario, inp, exp in perf_cases:
            tests.append({
                "id": scenario_id,
                "type": "Performance",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })

    def _add_error_handling_tests(self, tests, test_counter):
        error_cases = [
            ("Verify proper error message format", "Trigger 400 Bad Request", "JSON error with message, code, details"),
            ("Verify error codes follow API standards", "Invalid ID / Missing Auth", "Consistent use of 400, 401, 403, 404"),
            ("Verify stack traces are not exposed", "Trigger 500 Internal Error", "Generic error message, no stack trace"),
            ("Verify correlation / request ID present", "Any request", "Response header contains X-Request-ID")
        ]
        for scenario, inp, exp in error_cases:
            tests.append({
                "id": f"GET_ERR_{test_counter['id']:02d}",
                "type": "Negative",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

    def _add_compatibility_tests(self, tests, test_counter):
        compat_cases = [
            ("Verify backward compatibility", "Request with older API version header", "200 OK, returns data in old format"),
            ("Verify versioning behavior", "GET /v2/users vs GET /v1/users", "New fields present only in v2"),
            ("Verify behavior across environments", "QA/UAT/Prod config check", "Consistent behavior (endpoints, auth methods)")
        ]
        for scenario, inp, exp in compat_cases:
            tests.append({
                "id": f"GET_COMPAT_{test_counter['id']:02d}",
                "type": "Compatibility",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

    
    def _generate_default_testcases(self, method, payload):
        return [{
            "id": f"{method}_01",
            "type": "Positive",
            "scenario": "Default test case",
            "input": payload,
            "expected": "Success response"
        }]
    
    def _generate_query_param_testcases(self, endpoint, search_string, field_name="", method="GET", param_type="query", field_configs={}):
        tests = []
        
        # Determine param_name based on parameter type and user input
        if param_type == 'path':
            param_name = ""
        else:
            if field_name:
                param_name = field_name
            elif '=' in search_string:
                param_name = self._extract_param_name(search_string)
            else:
                param_name = ""
            
        param_values = self._extract_param_values(search_string)
        param_config = field_configs.get(param_name, {}) if param_name else {}
        is_required_param = get_required_status(param_config)
        
        test_counter = {"id": 1}
        method_upper = method.upper()
        method_prefix = method_upper[:3]
        expected_response = "204 No Content" if method_upper == "DELETE" else "200 OK"
        action_verb = "Delete" if method_upper == "DELETE" else "Get"
        
        # Path parameter specific design
        if param_type == 'path':
            base_endpoint = endpoint.rstrip('/')
            val = param_values[0] if param_values else (search_string if search_string else "123")
            
            # 1. Valid path param
            tests.append({
                "id": f"{method_prefix}_POS_01",
                "type": "Positive",
                "scenario": f"{action_verb} with existing ID {val}",
                "input": f"{base_endpoint}/{val}",
                "expected": expected_response
            })
            
            # 2. Non-existing ID
            tests.append({
                "id": f"{method_prefix}_NEG_01",
                "type": "Negative",
                "scenario": f"Verify {action_verb} returns 404 for non-existing ID",
                "input": f"{base_endpoint}/99999999",
                "expected": "404 Not Found"
            })
            
            # 3. Invalid ID format
            tests.append({
                "id": f"{method_prefix}_NEG_02",
                "type": "Negative",
                "scenario": f"Verify {action_verb} with invalid format ID (string instead of numeric)",
                "input": f"{base_endpoint}/invalid_id_format",
                "expected": "400 Bad Request"
            })
            
            # 4. Negative ID
            tests.append({
                "id": f"{method_prefix}_NEG_03",
                "type": "Negative",
                "scenario": f"Verify {action_verb} with negative ID",
                "input": f"{base_endpoint}/-1",
                "expected": "400 Bad Request / 404 Not Found"
            })
            
            # 5. Zero / null / blank ID
            tests.append({
                "id": f"{method_prefix}_NEG_04",
                "type": "Negative",
                "scenario": f"Verify {action_verb} with zero ID",
                "input": f"{base_endpoint}/0",
                "expected": "400 Bad Request / 404 Not Found"
            })
            
            # 6. Very large numeric ID
            tests.append({
                "id": f"{method_prefix}_NEG_05",
                "type": "Negative",
                "scenario": f"Verify {action_verb} with very large numeric ID (overflow)",
                "input": f"{base_endpoint}/9223372036854775807",
                "expected": "400 Bad Request / 404 Not Found"
            })
            
            # 7. Special characters in path param
            tests.append({
                "id": f"{method_prefix}_NEG_06",
                "type": "Negative",
                "scenario": f"Verify {action_verb} with special characters in path",
                "input": f"{base_endpoint}/ID_@#$%^&*",
                "expected": "400 Bad Request"
            })
            
            # 8. Security tests
            tests.append({
                "id": f"{method_prefix}_SEC_01",
                "type": "Security",
                "scenario": "Verify path parameter is protected against SQL Injection",
                "input": f"{base_endpoint}/{val}' OR '1'='1",
                "expected": "400 Bad Request"
            })
            tests.append({
                "id": f"{method_prefix}_SEC_02",
                "type": "Security",
                "scenario": "Verify path parameter is protected against XSS",
                "input": f"{base_endpoint}/<script>alert(1)</script>",
                "expected": "400 Bad Request"
            })
            tests.append({
                "id": f"{method_prefix}_SEC_03",
                "type": "Security",
                "scenario": "Verify path parameter is protected against Path Traversal",
                "input": f"{base_endpoint}/../../etc/passwd",
                "expected": "400 Bad Request / 404 Not Found"
            })
            
            # 9. Auth tests
            tests.append({
                "id": f"{method_prefix}_AUTH_01",
                "type": "Security",
                "scenario": "Verify 401 Unauthorized when accessing without valid token",
                "input": f"{base_endpoint}/{val}",
                "expected": "401 Unauthorized"
            })
            tests.append({
                "id": f"{method_prefix}_AUTH_02",
                "type": "Security",
                "scenario": "Verify 403 Forbidden when accessing resource without permission",
                "input": f"{base_endpoint}/{val}",
                "expected": "403 Forbidden"
            })
            
            # 10. Metadata tests
            tests.append({
                "id": f"{method_prefix}_MET_01",
                "type": "Performance",
                "scenario": "Verify response time is within acceptable limits ( < 500ms )",
                "input": f"{base_endpoint}/{val}",
                "expected": "Response time < 500ms"
            })
            tests.append({
                "id": f"{method_prefix}_MET_02",
                "type": "Header",
                "scenario": "Verify security headers are present in response (Content-Type, X-Content-Type-Options)",
                "input": f"{base_endpoint}/{val}",
                "expected": "Security headers present"
            })
            
            return tests

        # 1. Functional - Positive Scenarios (Query Params logic starts here)
        if param_values:
            for value in param_values:
                # If field_name is blank and search_string doesn't have '=', use as path parameter
                if not param_name:
                    # Handle as path parameter: append search_string to endpoint
                    base_endpoint = endpoint.rstrip('/')
                    inp = f"{base_endpoint}/{value}"
                    scen = f"{action_verb} with ID {value}"
                else:
                    inp = f"?{param_name}={value}"
                    scen = f"{action_verb} with {param_name}={value}"
                    
                tests.append({
                    "id": f"{method_prefix}_POS_{test_counter['id']:02d}",
                    "type": "Positive",
                    "scenario": scen,
                    "input": inp,
                    "expected": expected_response
                })
                test_counter["id"] += 1
            
            if len(param_values) > 1:
                if not param_name:
                    base_endpoint = endpoint.rstrip('/')
                    inp = f"{base_endpoint}/{param_values[0]}"
                else:
                    inp = f"?{param_name}={param_values[0]}&{param_name}={param_values[1]}"
                    
                tests.append({
                    "id": f"{method_prefix}_POS_{test_counter['id']:02d}",
                    "type": "Positive",
                    "scenario": f"Verify multiple query parameters work together" if param_name else f"Verify path parameter works",
                    "input": inp,
                    "expected": expected_response
                })
                test_counter["id"] += 1
        else:
            # If no param_values but we have search_string (and blank field_name)
            if not param_name and search_string:
                base_endpoint = endpoint.rstrip('/')
                inp = f"{base_endpoint}/{search_string}"
                scen = f"Valid path parameter {search_string}"
            else:
                inp = f"?{param_name if param_name else 'status'}=available"
                scen = f"Valid {param_name if param_name else 'status'} parameter"
                
            tests.append({
                "id": f"{method_prefix}_POS_{test_counter['id']:02d}",
                "type": "Positive",
                "scenario": scen,
                "input": inp,
                "expected": expected_response
            })
            test_counter["id"] += 1

        if is_required_param:
            tests.append({
                "id": f"{method_prefix}_NEG_{test_counter['id']:02d}",
                "type": "Negative",
                "scenario": f"Missing required query parameter {param_name}",
                "input": endpoint,
                "expected": "400 Bad Request"
            })
            test_counter["id"] += 1
        else:
            # Functional - Optional & Defaults
            tests.append({
                "id": f"{method_prefix}_FUN_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": f"Verify optional {param_name} parameter is truly optional",
                "input": endpoint,
                "expected": expected_response
            })
            test_counter["id"] += 1

        # 2. Pagination
        pagination_cases = [
            ("Verify page number works correctly", "page=2", "200 OK, returns second page"),
            ("Verify page size limit is enforced", "size=1000", "200 OK, capped at max size"),
            ("Verify page beyond max returns empty list", "page=999999", "200 OK, empty list []"),
            ("Verify total count value is correct", "page=1&size=10", "Response contains total count")
        ]
        for scenario, extra, exp in pagination_cases:
            val = param_values[0] if param_values else ('available' if not search_string else search_string)
            if not param_name:
                base_endpoint = endpoint.rstrip('/')
                inp = f"{base_endpoint}/{val}?{extra}"
            else:
                inp = f"?{param_name}={val}&{extra}"
            tests.append({
                "id": f"{method_prefix}_PAG_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # 3. Sorting
        sorting_cases = [
            ("Verify ascending sort works", "sort=name,asc", "200 OK, sorted A-Z"),
            ("Verify descending sort works", "sort=name,desc", "200 OK, sorted Z-A"),
            ("Verify invalid sort field returns error or default", "sort=invalid_field", "400 Bad Request or default sort"),
            ("Verify multi-field sorting", "sort=status,asc&sort=createdAt,desc", "200 OK")
        ]
        for scenario, extra, exp in sorting_cases:
            val = param_values[0] if param_values else ('available' if not search_string else search_string)
            if not param_name:
                base_endpoint = endpoint.rstrip('/')
                inp = f"{base_endpoint}/{val}?{extra}"
            else:
                inp = f"?{param_name}={val}&{extra}"
            tests.append({
                "id": f"{method_prefix}_SRT_{test_counter['id']:02d}",
                "type": "Functional",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # 4. Filtering
        val_to_use = param_values[0] if param_values else ('available' if not search_string else search_string)
        filtering_cases = [
            ("Negative", "Verify case sensitivity handling", f"{param_name if param_name else 'status'}={val_to_use.upper()}", "400 Bad Request"),
            ("Negative", "Verify partial match behavior", f"{param_name if param_name else 'status'}={val_to_use[:3] if len(val_to_use)>3 else 'ava'}", "400 Bad Request (partial match not supported)")
        ]
        for case_type, scenario, inp_param, exp in filtering_cases:
            if not param_name:
                base_endpoint = endpoint.rstrip('/')
                inp = f"{base_endpoint}/{val_to_use}?{inp_param}"
            else:
                inp = f"?{inp_param}"
            tests.append({
                "id": f"{method_prefix}_FLT_{test_counter['id']:02d}",
                "type": case_type,
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # 5. Negative Scenarios
        negative_cases = [
            (f"Invalid {param_name if param_name else 'parameter'} value", f"{param_name if param_name else ''}=invalid_val_@#$%", "400 Bad Request"),
            (f"Duplicate {param_name if param_name else 'parameter'} parameters handling", f"{param_name if param_name else ''}=val1&{param_name if param_name else ''}=val2", "200 OK (first/last used) or 400"),
            (f"Extremely large query values in {param_name if param_name else 'parameter'}", f"{param_name if param_name else ''}={'x' * 2000}", "400 Bad Request / 414 URL Too Long or Fallback to 200 OK"),
            (f"Invalid query param name", "invalid_param=some_value", "200 OK (ignored) or 400")
        ]
        for scenario, inp_param, exp in negative_cases:
            if not param_name and inp_param.startswith('='):
                inp = f"?{inp_param[1:]}"
            else:
                inp = f"?{inp_param}"
                
            tests.append({
                "id": f"{method_prefix}_NEG_{test_counter['id']:02d}",
                "type": "Negative",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # 6. Security
        security_cases = [
            (f"SQL injection in {param_name if param_name else 'parameter'}", f"{param_name if param_name else ''}=1' OR '1'='1", "400 Bad Request"),
            (f"XSS injection in {param_name if param_name else 'parameter'}", f"{param_name if param_name else ''}=<script>alert(1)</script>", "400 Bad Request"),
            (f"Path traversal in {param_name if param_name else 'parameter'}", f"{param_name if param_name else ''}=../../etc/passwd", "400 Bad Request"),
        ]
        for scenario, inp_param, exp in security_cases:
            if not param_name and inp_param.startswith('='):
                inp = f"?{inp_param[1:]}"
            else:
                inp = f"?{inp_param}"
                
            tests.append({
                "id": f"{method_prefix}_SEC_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # 7. Authorization & Access Control
        auth_cases = [
            ("Verify user can access own data", "Valid user token", "200 OK"),
            ("Verify user cannot access others' data", "User A token accessing User B data", "403 Forbidden / 404 Not Found"),
            ("Verify role-based access control", "Regular user accessing admin resource", "403 Forbidden / 404 Not Found"),
            ("Verify expired token handling", "Expired JWT token", "401 Unauthorized"),
            ("Verify revoked token handling", "Revoked/Blacklisted token", "401 Unauthorized")
        ]
        for scenario, inp, exp in auth_cases:
            tests.append({
                "id": f"{method_prefix}_AUTH_{test_counter['id']:02d}",
                "type": "Security",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        # 8. Performance & Reliability
        perf_cases = [
            ("Verify response time under load", "100 concurrent requests", "Response time within SLA"),
            ("Verify rate limiting behavior", "Exceeding requests per minute limit", "429 Too Many Requests"),
            ("Verify caching headers (ETag / Cache-Control)", "Repeated GET request", "200 OK / 304 Not Modified")
        ]
        for scenario, inp, exp in perf_cases:
            tests.append({
                "id": f"{method_prefix}_PERF_{test_counter['id']:02d}",
                "type": "Performance",
                "scenario": scenario,
                "input": inp,
                "expected": exp
            })
            test_counter["id"] += 1

        return tests

    
    def _extract_param_name(self, search_string):
        search_string = search_string.strip()
        if '=' in search_string:
            param_name = search_string.split('=')[0].strip('?').strip()
            return param_name if param_name else 'status'
        return 'status'
    
    def _extract_param_values(self, search_string):
        search_string = search_string.strip()
        
        if not search_string:
            return []
        
        if '=' in search_string:
            values_part = search_string.split('=', 1)[1].strip()
        else:
            values_part = search_string
        
        if not values_part:
            return []
        
        values = [v.strip() for v in values_part.split(',') if v.strip()]
        return values


def detect_field_type(field_name, value):
    field_lower = field_name.lower()
    
    if value is None:
        return 'null'
    elif isinstance(value, bool):
        return 'boolean'
    elif isinstance(value, int):
        return 'integer'
    elif isinstance(value, float):
        return 'number'
    elif isinstance(value, list):
        return 'array'
    elif isinstance(value, dict):
        return 'object'
    elif isinstance(value, str):
        if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', value):
            return 'email'
        elif re.match(r'^\d{10}$|^\+\d{1,3}\d{9,14}$|^\d{3}-\d{3}-\d{4}$', value):
            return 'phone'
        elif re.match(r'^\d{4}-\d{2}-\d{2}', value):
            return 'date'
        elif re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value):
            return 'datetime'
        elif re.match(r'^[A-Z0-9]{8,}$', value):
            return 'id'
        elif 'password' in field_lower or 'pwd' in field_lower or 'secret' in field_lower:
            return 'password'
        elif 'url' in field_lower or 'link' in field_lower:
            return 'url'
        elif 'uuid' in field_lower or 'guid' in field_lower:
            return 'uuid'
        else:
            return 'string'
    else:
        return 'string'

def generate_nested_array_tests(field_name, array_value, counter, method):
    tests = []
    
    if not isinstance(array_value, list) or len(array_value) == 0:
        return tests
    
    first_element = array_value[0]
    
    if isinstance(first_element, dict):
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array with object missing required fields - {field_name}",
            "input": {field_name: [{}]},
            "expected": "400 Object in array missing required fields"
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array with object having extra fields - {field_name}",
            "input": {field_name: [{**first_element, "extra_field": "unexpected"}]},
            "expected": "200 Success or 400 Unknown field in array object"
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array with invalid nested field types - {field_name}",
            "input": {field_name: [{**{k: None for k in first_element.keys()}}]},
            "expected": "400 Invalid field type in nested array object"
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array with duplicate objects - {field_name}",
            "input": {field_name: [first_element, first_element]},
            "expected": "200 Success or 400 Duplicate objects not allowed"
        })
        counter['id'] += 1
        
        for key, val in first_element.items():
            if isinstance(val, str) and '@' in val:
                tests.append({
                    "id": f"{method}_NESTED_{counter['id']:03d}",
                    "type": "Validation",
                    "scenario": f"Array of objects with invalid email in nested field {key} - {field_name}",
                    "input": {field_name: [{**first_element, key: "invalid-email"}]},
                    "expected": "400 Invalid email in nested array object field"
                })
                counter['id'] += 1
                
    elif isinstance(first_element, list):
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array of arrays - {field_name}",
            "input": {field_name: [[1, 2], [3, 4]]},
            "expected": "200 Success or 400 Nested arrays not allowed"
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array of arrays with null - {field_name}",
            "input": {field_name: [[1, None]]},
            "expected": "400 Null values in nested array"
        })
        counter['id'] += 1
        
    else:
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array with duplicate values - {field_name}",
            "input": {field_name: [first_element, first_element]},
            "expected": "200 Success or 400 Duplicate values not allowed"
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_NESTED_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Array with mixed types - {field_name}",
            "input": {field_name: [first_element, "string", 123, True]},
            "expected": "400 Mixed types in array not allowed"
        })
        counter['id'] += 1
        
        if isinstance(first_element, int):
            tests.append({
                "id": f"{method}_NESTED_{counter['id']:03d}",
                "type": "Validation",
                "scenario": f"Array of integers with string value - {field_name}",
                "input": {field_name: [first_element, "not_a_number"]},
                "expected": "400 Invalid type in numeric array"
            })
            counter['id'] += 1
    
    return tests

def generate_field_specific_tests(field_name, field_type, value, counter, method, required=False, config=None):
    tests = []
    success_code = _success_expected(method)
    config = config if isinstance(config, dict) else {}
    
    field_id = field_name.replace('.', '_')

    # Add missing field test if it's required
    # Only if NOT POST/PUT/PATCH as they handle it more thoroughly in their specific methods
    if required and method not in ['POST', 'PUT', 'PATCH']:
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Missing required field {field_name}",
            "input": f"Remove {field_name} from payload",
            "expected": f"400 Bad Request - {field_name} is required"
        })
        counter['id'] += 1
    
    if field_type == 'email':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Invalid email format for {field_name}",
            "input": {field_name: "invalid-email"},
            "expected": "400 Invalid email format"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Missing @ symbol in email {field_name}",
            "input": {field_name: "invalidemail.com"},
            "expected": "400 Invalid email format"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Missing domain in email {field_name}",
            "input": {field_name: "test@"},
            "expected": "400 Invalid email format"
        })
        counter['id'] += 1
        
    elif field_type == 'phone':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Invalid phone format for {field_name}",
            "input": {field_name: "123"},
            "expected": "400 Invalid phone format"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Phone with invalid characters {field_name}",
            "input": {field_name: "123-ABC-DEFG"},
            "expected": "400 Invalid phone format"
        })
        counter['id'] += 1
        
    elif field_type == 'date':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Invalid date format for {field_name}",
            "input": {field_name: "2024/12/08"},
            "expected": "400 Invalid date format (YYYY-MM-DD)"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Future date for {field_name}",
            "input": {field_name: "2099-12-31"},
            "expected": "400 Invalid date - future date not allowed"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Invalid day in date for {field_name}",
            "input": {field_name: "2024-02-30"},
            "expected": "400 Invalid date - day out of range"
        })
        counter['id'] += 1
        
    elif field_type == 'integer':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Positive",
            "scenario": f"Valid integer for {field_name}",
            "input": {field_name: 100},
            "expected": success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if required else "Positive",
            "scenario": f"Zero value for {field_name}",
            "input": {field_name: 0},
            "expected": "400 Invalid value or 200 OK" if required else success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Negative value for {field_name}",
            "input": {field_name: -1},
            "expected": "200 OK / 400 Invalid"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Boundary: Max integer for {field_name}",
            "input": {field_name: 2147483647},
            "expected": "200 Success or 400 Overflow"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Float value for integer field {field_name}",
            "input": {field_name: 123.45},
            "expected": "400 Invalid data type - integer expected"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Very large number for {field_name}",
            "input": {field_name: 999999999999999999},
            "expected": "400 Value out of range"
        })
        counter['id'] += 1
        
    elif field_type == 'number':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"String value for number field {field_name}",
            "input": {field_name: "not a number"},
            "expected": "400 Invalid data type - number expected"
        })
        counter['id'] += 1
        
    elif field_type == 'string':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Positive",
            "scenario": f"Valid string for {field_name}",
            "input": {field_name: "valid_string_value"},
            "expected": success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if required else "Positive",
            "scenario": f"Empty string for {field_name}",
            "input": {field_name: ""},
            "expected": "400 Invalid value or 200 OK" if required else success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if required else "Positive",
            "scenario": f"Whitespace only for {field_name}",
            "input": {field_name: "   "},
            "expected": "400 Invalid value - whitespace only" if required else success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Positive",
            "scenario": f"Numeric string for {field_name}",
            "input": {field_name: "12345"},
            "expected": success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Positive",
            "scenario": f"String with special characters for {field_name}",
            "input": {field_name: "test!@#$%^&*()_+"},
            "expected": success_code
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Very long string for {field_name}",
            "input": {field_name: "s" * 5000},
            "expected": "400 String too long / 200 If accepted"
        })
        counter['id'] += 1
        min_length = max(1, len(str(value)) // 2)
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if required else "Positive",
            "scenario": f"Minimum length boundary for {field_name}",
            "input": {field_name: "a"},
            "expected": f"400 Too short (min: {min_length})" if required else success_code
        })
        counter['id'] += 1
        
    elif field_type == 'uuid':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Invalid UUID format for {field_name}",
            "input": {field_name: "invalid-uuid-format"},
            "expected": "400 Invalid UUID format"
        })
        counter['id'] += 1
        
    elif field_type == 'url':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"Invalid URL format for {field_name}",
            "input": {field_name: "not a valid url"},
            "expected": "400 Invalid URL format"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation",
            "scenario": f"URL with invalid protocol for {field_name}",
            "input": {field_name: "ftp://invalid.com"},
            "expected": "400 Invalid URL - only HTTP/HTTPS allowed"
        })
        counter['id'] += 1
        
    elif field_type == 'password':
        tests.append({
            "id": f"{method}_SEC_{counter['id']:03d}",
            "type": "Security",
            "scenario": f"Weak password for {field_name}",
            "input": {field_name: "123"},
            "expected": "400 Password too weak (min 8 chars, numbers, letters)"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_SEC_{counter['id']:03d}",
            "type": "Security",
            "scenario": f"Password without special chars {field_name}",
            "input": {field_name: "validpass123"},
            "expected": "400 Password must contain special characters"
        })
        counter['id'] += 1
        
    elif field_type == 'array':
        multiple_items_is_invalid = _field_leaf_name(field_name) == 'tags'
        null_items_are_invalid = not _array_items_allow_null(config)
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if multiple_items_is_invalid else "Positive",
            "scenario": f"Multiple items in array for {field_name}",
            "input": {field_name: [value[0] if isinstance(value, list) and value else "item1", "item2"]},
            "expected": "400 Bad Request" if multiple_items_is_invalid else "200 OK"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if required else "Positive",
            "scenario": f"Empty array for {field_name}",
            "input": {field_name: []},
            "expected": "400 Empty array not allowed" if required else "200 Success"
        })
        counter['id'] += 1
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if null_items_are_invalid else "Positive",
            "scenario": f"Null element in array {field_name}",
            "input": {field_name: [None]},
            "expected": "400 Array contains null elements" if null_items_are_invalid else success_code
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Non-array value for {field_name}",
            "input": {field_name: "not an array"},
            "expected": "400 Invalid data type - array expected"
        })
        counter['id'] += 1
        
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Array exceeds max length for {field_name}",
            "input": {field_name: list(range(1000))},
            "expected": "400 Array size exceeds maximum limit"
        })
        counter['id'] += 1
        
        nested_tests = generate_nested_array_tests(field_name, value, counter, method)
        tests.extend(nested_tests)
        
    elif field_type == 'boolean':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Non-boolean value for {field_name}",
            "input": {field_name: "not-a-boolean"},
            "expected": "400 Invalid data type - boolean expected"
        })
        counter['id'] += 1

    elif field_type == 'object':
        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Negative",
            "scenario": f"Non-object value for {field_name}",
            "input": {field_name: "not-an-object"},
            "expected": "400 Invalid data type - object expected"
        })
        counter['id'] += 1

        tests.append({
            "id": f"{method}_VAL_{counter['id']:03d}",
            "type": "Validation" if required else "Positive",
            "scenario": f"Empty object for {field_name}",
            "input": {field_name: {}},
            "expected": "400 Missing required sub-fields" if required else "200 Success / 400 Missing required"
        })
        counter['id'] += 1
        
    return tests

def flatten(data, parent_key="", sep="."):
    """
    Flatten a nested dict/list structure into dot-notation keys.
    Supports arrays by including the first element index (.0).
    """
    items = {}
    if isinstance(data, dict):
        for k, v in data.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
            if isinstance(v, dict):
                items.update(flatten(v, new_key, sep))
            elif isinstance(v, list):
                # Keep the array itself
                items[new_key] = v
                # If array contains objects, flatten the first object with index 0
                if len(v) > 0 and isinstance(v[0], dict):
                    items.update(flatten(v[0], f"{new_key}.0", sep))
            else:
                items[new_key] = v
    return items

def remove_field(data, field_path):
    new_data = json.loads(json.dumps(data))
    keys = field_path.split(".")
    d = new_data
    for k in keys[:-1]:
        if isinstance(d, dict):
            d = d.get(k, {})
        elif isinstance(d, list):
            d = d[int(k)]
        else:
            return new_data
    if isinstance(d, dict):
        d.pop(keys[-1], None)
    elif isinstance(d, list):
        try:
            d.pop(int(keys[-1]))
        except:
            pass
    return new_data

def set_field(data, field_path, value):
    new_data = json.loads(json.dumps(data))
    keys = field_path.split(".")
    d = new_data
    for index, k in enumerate(keys[:-1]):
        next_key = keys[index + 1]
        if isinstance(d, dict):
            if k not in d or d[k] is None:
                d[k] = [] if next_key.isdigit() else {}
            d = d[k]
        elif isinstance(d, list):
            try:
                item_index = int(k)
            except (TypeError, ValueError):
                return new_data
            while len(d) <= item_index:
                d.append({} if not next_key.isdigit() else [])
            if d[item_index] is None:
                d[item_index] = [] if next_key.isdigit() else {}
            d = d[item_index]
        else:
            return new_data
    if isinstance(d, dict):
        d[keys[-1]] = value
    elif isinstance(d, list):
        try:
            item_index = int(keys[-1])
            while len(d) <= item_index:
                d.append(None)
            d[item_index] = value
        except (TypeError, ValueError):
            return new_data
    return new_data

# Backward-compatible alias for PEP 8 rename
generate_testcases = GenerateTestcases
