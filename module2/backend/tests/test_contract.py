"""The backend matches module2/openapi.yaml, the contract the frontend was built against.

- Every operation in the spec is implemented at the same method and path, and
  the app serves nothing the spec does not describe.
- A scenario drives all 15 operations, including their documented error
  responses. Each response's status code must be listed for that operation, and
  its body must validate against the schema the spec gives it.

The validator below covers the JSON Schema keywords openapi.yaml uses. It is
strict about unexpected properties, so a response that leaks an undocumented
field such as `password_hash` fails.
"""

import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from tests.conftest import party, restaurant_body

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi.yaml"
SPEC = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def spec_operations() -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in SPEC["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


# ---- a minimal JSON Schema validator for the subset openapi.yaml uses -------


def resolve(schema: dict) -> dict:
    while "$ref" in schema:
        node = SPEC
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def declared_properties(schema: dict) -> dict:
    schema = resolve(schema)
    properties = dict(schema.get("properties", {}))
    for part in schema.get("allOf", []):
        properties.update(declared_properties(part))
    return properties


def required_properties(schema: dict) -> set[str]:
    schema = resolve(schema)
    required = set(schema.get("required", []))
    for part in schema.get("allOf", []):
        required |= required_properties(part)
    return required


TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate(value, schema: dict, where: str = "$") -> list[str]:
    schema = resolve(schema)
    errors: list[str] = []

    types = schema.get("type")
    if types is None and ("properties" in schema or "allOf" in schema):
        types = "object"
    if types is not None:
        allowed = [types] if isinstance(types, str) else types
        if not any(TYPE_CHECKS[t](value) for t in allowed):
            return [f"{where}: expected {allowed}, got {value!r}"]

    if value is None:
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, str) and schema.get("format") == "date-time":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                errors.append(f"{where}: date-time {value!r} has no timezone")
        except ValueError:
            errors.append(f"{where}: {value!r} is not a date-time")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: {value} > maximum {schema['maximum']}")

    if isinstance(value, dict):
        properties = declared_properties(schema)
        for name in required_properties(schema) - value.keys():
            errors.append(f"{where}: missing required property {name!r}")
        extra_schema = schema.get("additionalProperties")
        for name, item in value.items():
            if name in properties:
                errors += validate(item, properties[name], f"{where}.{name}")
            elif isinstance(extra_schema, dict):
                errors += validate(item, extra_schema, f"{where}.{name}")
            else:
                errors.append(f"{where}: undocumented property {name!r}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += validate(item, schema["items"], f"{where}[{index}]")
    return errors


# ---- the recording client ----------------------------------------------------


def match_operation(method: str, path: str) -> tuple[str, dict]:
    for template, item in SPEC["paths"].items():
        pattern = "^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$"
        if re.match(pattern, path) and method.lower() in item:
            return template, item[method.lower()]
    raise AssertionError(f"{method} {path} is not in openapi.yaml")


class ContractChecker:
    def __init__(self, api):
        self.api = api
        self.seen: set[tuple[str, str]] = set()
        self.statuses: set[tuple[str, str, int]] = set()

    def call(self, method: str, path: str, *, expect: int, **kwargs):
        response = getattr(self.api, method)(path, **kwargs)
        template, operation = match_operation(method, path)
        self.seen.add((method.upper(), template))
        self.statuses.add((method.upper(), template, response.status_code))

        assert response.status_code == expect, f"{method.upper()} {path}: {response.status_code} {response.text}"
        documented = operation["responses"]
        assert str(response.status_code) in documented, (
            f"{method.upper()} {template} returned {response.status_code}, "
            f"which openapi.yaml does not list ({sorted(documented)})"
        )
        spec_response = resolve(documented[str(response.status_code)])
        schema = spec_response["content"]["application/json"]["schema"]
        errors = validate(response.json(), schema)
        assert not errors, f"{method.upper()} {template} {response.status_code}:\n" + "\n".join(errors)

        # Protected operations must say so in the spec, and vice versa.
        protected = bool(operation.get("security"))
        assert protected == ("x-required-role" in operation), template
        return response.json()


# ---- tests -------------------------------------------------------------------


def implemented_operations(api) -> dict[tuple[str, str], str]:
    """(METHOD, path without /api) -> operationId, from the app's own generated OpenAPI."""
    generated = api.client.app.openapi()["paths"]
    return {
        (method.upper(), path.removeprefix("/api")): operation["operationId"]
        for path, item in generated.items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    }


def test_every_spec_operation_is_implemented_and_nothing_else(api):
    assert set(implemented_operations(api)) == spec_operations()


def test_operation_ids_match_the_spec(api):
    implemented = implemented_operations(api)
    for path, item in SPEC["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                assert implemented[(method.upper(), path)] == operation["operationId"]


def test_frontend_service_interface_matches_the_spec():
    """WaitWiseService.ts annotates each method with its endpoint; they must agree with the spec.

    The frontend's HTTP service test checks its requests against the same
    annotations, which closes the loop: frontend -> annotations -> spec -> backend.
    """
    source = (SPEC_PATH.parent / "frontend/src/services/WaitWiseService.ts").read_text(encoding="utf-8")
    annotated = {
        name: (method, path)
        for method, path, name in re.findall(
            r"/\*\*\s*(GET|POST|PUT|PATCH|DELETE) /api(\S+)[^*]*\*/\s*(\w+)\(", source
        )
    }
    in_spec = {
        operation["x-service-method"]: (method.upper(), path)
        for path, item in SPEC["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    }
    assert annotated == in_spec


def test_servers_point_at_the_agreed_port():
    assert SPEC["servers"][0]["url"] == "http://localhost:9127/api"


def test_full_scenario_matches_the_spec(api):
    c = ContractChecker(api)

    # Auth
    c.call("post", "/auth/login", json={"username": "ghost", "password": "password"}, expect=401)
    c.call("post", "/auth/login", json={"username": "", "password": ""}, expect=422)
    staff = {"Authorization": f"Bearer {c.call('post', '/auth/login', json={'username': 'bluebird', 'password': 'password'}, expect=200)['token']}"}
    admin = {"Authorization": f"Bearer {c.call('post', '/auth/login', json={'username': 'admin', 'password': 'password'}, expect=200)['token']}"}

    # Public restaurants
    c.call("get", "/restaurants", expect=200)
    c.call("get", "/restaurants/1", expect=200)
    c.call("get", "/restaurants/999", expect=404)

    # Eater waitlist
    joined = c.call("post", "/restaurants/1/waitlist", json=party(), expect=201)
    c.call("post", "/restaurants/1/waitlist", json=party(party_size=0), expect=422)
    c.call("post", "/restaurants/999/waitlist", json=party(), expect=404)
    c.call("get", f"/waitlist/{joined['public_token']}", expect=200)
    c.call("get", "/waitlist/nope", expect=404)
    c.call("delete", f"/waitlist/{joined['public_token']}", expect=200)
    c.call("delete", f"/waitlist/{joined['public_token']}", expect=409)
    c.call("delete", "/waitlist/nope", expect=404)

    # Restaurant operations
    c.call("get", "/restaurant/waitlist", expect=401)
    c.call("get", "/restaurant/waitlist", headers=admin, expect=403)
    board = c.call("get", "/restaurant/waitlist", headers=staff, expect=200)
    c.call("post", "/restaurant/waitlist", json=party(), headers=staff, expect=201)
    c.call("post", "/restaurant/waitlist", json=party(mobile_phone="x"), headers=staff, expect=422)
    c.call("post", "/restaurant/waitlist", json=party(), expect=401)
    c.call("post", "/restaurant/waitlist", json=party(), headers=admin, expect=403)
    waiting, notified = board["active"][1], board["active"][0]
    c.call("patch", f"/restaurant/waitlist/{waiting['id']}", json={"status": "NOTIFIED"}, headers=staff, expect=200)
    c.call("patch", f"/restaurant/waitlist/{notified['id']}", json={"status": "NOTIFIED"}, headers=staff, expect=422)
    c.call("patch", f"/restaurant/waitlist/{waiting['id']}", json={"status": "SEATED"}, headers=staff, expect=200)
    c.call("patch", f"/restaurant/waitlist/{waiting['id']}", json={"status": "SEATED"}, headers=staff, expect=409)
    c.call("patch", "/restaurant/waitlist/999", json={"status": "SEATED"}, headers=staff, expect=404)
    c.call("patch", "/restaurant/waitlist/1", json={"status": "SEATED"}, expect=401)
    c.call("patch", "/restaurant/waitlist/1", json={"status": "SEATED"}, headers=admin, expect=403)
    c.call("patch", "/restaurant/settings", json={"current_wait_minutes": 45}, headers=staff, expect=200)
    c.call("patch", "/restaurant/settings", json={"current_wait_minutes": -1}, headers=staff, expect=422)
    c.call("patch", "/restaurant/settings", json={"online_waitlist_enabled": False}, expect=401)
    c.call("patch", "/restaurant/settings", json={"online_waitlist_enabled": False}, headers=admin, expect=403)
    c.call("patch", "/restaurant/settings", json={"online_waitlist_enabled": False}, headers=staff, expect=200)
    c.call("post", "/restaurants/1/waitlist", json=party(), expect=409)

    # Admin
    c.call("get", "/admin/restaurants", headers=admin, expect=200)
    c.call("get", "/admin/restaurants", expect=401)
    c.call("get", "/admin/restaurants", headers=staff, expect=403)
    created = c.call("post", "/admin/restaurants", json=restaurant_body(), headers=admin, expect=201)
    c.call("post", "/admin/restaurants", json=restaurant_body(), headers=admin, expect=409)
    c.call("post", "/admin/restaurants", json=restaurant_body(name=""), headers=admin, expect=422)
    c.call("post", "/admin/restaurants", json=restaurant_body(), expect=401)
    c.call("post", "/admin/restaurants", json=restaurant_body(), headers=staff, expect=403)
    rid = created["id"]
    c.call("get", f"/admin/restaurants/{rid}", headers=admin, expect=200)
    c.call("get", "/admin/restaurants/999", headers=admin, expect=404)
    c.call("get", f"/admin/restaurants/{rid}", expect=401)
    c.call("get", f"/admin/restaurants/{rid}", headers=staff, expect=403)
    c.call("put", f"/admin/restaurants/{rid}", json=restaurant_body(password=""), headers=admin, expect=200)
    c.call("put", f"/admin/restaurants/{rid}", json=restaurant_body(username="oakember"), headers=admin, expect=409)
    c.call("put", f"/admin/restaurants/{rid}", json=restaurant_body(phone=""), headers=admin, expect=422)
    c.call("put", "/admin/restaurants/999", json=restaurant_body(), headers=admin, expect=404)
    c.call("put", f"/admin/restaurants/{rid}", json=restaurant_body(), expect=401)
    c.call("put", f"/admin/restaurants/{rid}", json=restaurant_body(), headers=staff, expect=403)
    c.call("patch", f"/admin/restaurants/{rid}/status", json={"is_active": False}, headers=admin, expect=200)
    c.call("patch", f"/admin/restaurants/{rid}/status", json={}, headers=admin, expect=422)
    c.call("patch", "/admin/restaurants/999/status", json={"is_active": True}, headers=admin, expect=404)
    c.call("patch", f"/admin/restaurants/{rid}/status", json={"is_active": True}, expect=401)
    c.call("patch", f"/admin/restaurants/{rid}/status", json={"is_active": True}, headers=staff, expect=403)

    assert c.seen == spec_operations(), f"scenario skipped: {spec_operations() - c.seen}"

    # Every response code the spec documents was actually produced.
    documented = {
        (method.upper(), path, int(code))
        for path, item in SPEC["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
        for code in operation["responses"]
    }
    assert documented - c.statuses == set()


@pytest.mark.parametrize(
    ("value", "schema_name", "problem"),
    [
        ({"code": "NOPE", "message": "m", "field_errors": {}}, "Error", "not in"),
        ({"code": "VALIDATION", "message": "m"}, "Error", "missing required"),
        ({"id": 1, "username": "a", "role": "ADMIN", "restaurant_id": None, "password_hash": "x"}, "SessionUser", "undocumented"),
        ({"id": "1", "username": "a", "role": "ADMIN", "restaurant_id": None}, "SessionUser", "expected"),
    ],
)
def test_the_validator_itself_catches_bad_bodies(value, schema_name, problem):
    errors = validate(value, {"$ref": f"#/components/schemas/{schema_name}"})
    assert any(problem in e for e in errors), errors
