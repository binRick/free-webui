"""OpenAPI tool servers: CRUD, spec parsing, and the compose+dispatch loop."""
import httpx


async def _signup(client):
    await client.post("/api/auth/setup", json={"username": "alice", "password": "hunter22hunter"})


# ---- CRUD ----

async def test_openapi_server_crud(client):
    await _signup(client)
    assert (await client.get("/api/openapi_servers")).json() == []
    created = (
        await client.post(
            "/api/openapi_servers",
            json={"name": "wx", "url": "http://tools.test/openapi.json", "headers": {"x-key": "k"}},
        )
    ).json()
    sid = created["id"]
    assert created["enabled"] is True and created["headers"] == {"x-key": "k"}
    assert [s["id"] for s in (await client.get("/api/openapi_servers")).json()] == [sid]
    assert (await client.patch(f"/api/openapi_servers/{sid}", json={"enabled": False})).json()[
        "enabled"
    ] is False
    assert (await client.delete(f"/api/openapi_servers/{sid}")).status_code == 204
    assert (await client.get("/api/openapi_servers")).json() == []


# ---- spec parsing (pure) ----

def test_spec_to_tools_parses_params_and_body():
    from app.openapi_tools import spec_to_tools

    spec = {
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "summary": "Get an item",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
                    ],
                },
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"title": {"type": "string"}},
                                    "required": ["title"],
                                }
                            }
                        }
                    },
                },
            }
        },
    }
    tools, ops = spec_to_tools(spec, "https://api.example.com/openapi.json", 5, "demo")
    by_name = {t["function"]["name"]: t["function"] for t in tools}
    assert {"openapi_5_getItem", "openapi_5_createItem"} <= set(by_name)
    get = by_name["openapi_5_getItem"]
    assert set(get["parameters"]["properties"]) == {"id", "verbose"}
    assert get["parameters"]["required"] == ["id"]
    assert ops["openapi_5_getItem"]["loc"] == {"id": "path", "verbose": "query"}
    assert ops["openapi_5_getItem"]["base_url"] == "https://api.example.com"
    assert ops["openapi_5_createItem"]["loc"]["title"] == "body"


def test_spec_to_tools_resolves_refs_and_synthesizes_ids():
    from app.openapi_tools import spec_to_tools

    spec = {
        "components": {"parameters": {"Q": {"name": "q", "in": "query", "schema": {"type": "string"}}}},
        "paths": {"/search": {"get": {"parameters": [{"$ref": "#/components/parameters/Q"}]}}},
    }
    tools, ops = spec_to_tools(spec, "http://x.test/spec", 1, "s")
    # no operationId -> synthesized from method_path; ref param resolved
    name = tools[0]["function"]["name"]
    assert name.startswith("openapi_1_")
    assert "q" in tools[0]["function"]["parameters"]["properties"]
    assert ops[name]["loc"]["q"] == "query"


# ---- compose + dispatch ----

async def test_openapi_compose_and_dispatch(client, monkeypatch):
    await _signup(client)
    me = (await client.get("/api/auth/me")).json()["id"]
    sid = (
        await client.post(
            "/api/openapi_servers", json={"name": "wx", "url": "http://tools.test/openapi.json"}
        )
    ).json()["id"]

    spec = {
        "servers": [{"url": "http://tools.test"}],
        "paths": {
            "/weather": {
                "get": {
                    "operationId": "getWeather",
                    "summary": "Current weather",
                    "parameters": [{"name": "city", "in": "query", "schema": {"type": "string"}}],
                }
            }
        },
    }
    op_calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host != "tools.test":
            return httpx.Response(404)
        if req.url.path == "/openapi.json":
            return httpx.Response(200, json=spec)
        if req.url.path == "/weather":
            op_calls.append(dict(req.url.params))
            return httpx.Response(200, json={"tempC": 18})
        return httpx.Response(404)

    Orig = httpx.AsyncClient

    class Patched(Orig):
        def __init__(self, *a, **k):
            k.setdefault("transport", httpx.MockTransport(handler))
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)

    from app.main import app
    from app.mcp import compose_tool_specs, run_tool

    db = app.state.db
    specs, dispatch = await compose_tool_specs(db, me)
    names = [s["function"]["name"] for s in specs]
    assert f"openapi_{sid}_getWeather" in names  # merged alongside built-ins

    result = await run_tool(db, me, dispatch, f"openapi_{sid}_getWeather", {"city": "Brooklyn"})
    assert "18" in result  # the operation response body is returned
    assert op_calls and op_calls[0].get("city") == "Brooklyn"  # query param forwarded


async def test_openapi_unknown_or_removed_server(client, monkeypatch):
    await _signup(client)
    me = (await client.get("/api/auth/me")).json()["id"]
    from app.main import app
    from app.mcp import run_tool

    # an openapi dispatch entry whose server was deleted -> graceful error string
    result = await run_tool(
        app.state.db, me, {"openapi_99_x": ("openapi", 99, {"method": "get", "path": "/x", "base_url": "http://t", "loc": {}})},
        "openapi_99_x", {},
    )
    assert "no longer configured" in result
