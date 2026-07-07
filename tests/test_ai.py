"""WorldAI-Bausteine: Result-Store, compute-Sandbox, Schreib-Klassifikation,
Schema-Generierung aus der Registry, Provider-Auswahl."""

import json
import time
import uuid

import pytest

from weltmodell.ai import compute, providers, results, sessions, tools
from weltmodell.ai.compute import ComputeError
from weltmodell.errors import NotFoundError, ValidationError
from weltmodell.mcp_server import mcp

UNKNOWN_REF = f"ref:{uuid.uuid4()}"


@pytest.fixture
def session_id(conn):
    return str(sessions.create_session(conn)["id"])


# --- Result-Store ---------------------------------------------------------------


class TestResultStore:
    def test_small_result_stays_inline(self, conn, session_id):
        value = {"ids": ["a", "b"], "total": 2}
        out, offloaded = results.offload_if_large(
            conn, session_id, "call_1", value, threshold=8000
        )
        assert not offloaded
        assert out == value

    def test_large_result_offloads_with_ref_summary_sample(self, conn, session_id):
        value = {"ids": [str(uuid.uuid4()) for _ in range(200)], "total": 200}
        out, offloaded = results.offload_if_large(
            conn, session_id, "call_1", value, threshold=100
        )
        assert offloaded
        assert out["ref"].startswith("ref:")
        assert "200" in out["summary"]
        assert out["sample"]["ids"] == value["ids"][:10]
        # Ref löst auf das VOLLE Ergebnis auf.
        assert results.resolve_ref(conn, session_id, out["ref"]) == value

    def test_threshold_from_env(self, monkeypatch):
        monkeypatch.setenv("WORLDAI_RESULT_THRESHOLD", "123")
        assert results.get_threshold() == 123
        monkeypatch.delenv("WORLDAI_RESULT_THRESHOLD")
        assert results.get_threshold() == results.DEFAULT_THRESHOLD

    def test_unknown_ref_raises(self, conn, session_id):
        with pytest.raises(NotFoundError):
            results.resolve_ref(conn, session_id, UNKNOWN_REF)
        with pytest.raises(NotFoundError):
            results.resolve_ref(conn, session_id, "ref:kaputt")

    def test_ref_is_session_scoped(self, conn, session_id):
        other = str(sessions.create_session(conn)["id"])
        digest = results.store_result(conn, session_id, None, [1, 2, 3])
        with pytest.raises(NotFoundError):
            results.resolve_ref(conn, other, digest["ref"])


# --- compute-Sandbox ------------------------------------------------------------


class TestCompute:
    def test_intersection_of_injected_id_lists(self):
        a = [f"id{i}" for i in range(50)]
        b = [f"id{i}" for i in range(25, 75)]
        out = compute.run_compute(
            "const s = new Set(b); return a.filter(x => s.has(x));",
            {"a": a, "b": b},
        )
        assert out == [f"id{i}" for i in range(25, 50)]

    def test_timeout_kills_endless_loop(self):
        started = time.monotonic()
        with pytest.raises(ComputeError):
            compute.run_compute("while (true) {}", {})
        assert time.monotonic() - started < compute.TIME_LIMIT_SECONDS + 2

    @pytest.mark.parametrize("code", [
        "return require('fs').readFileSync('/etc/passwd')",
        "return fetch('http://example.org')",
        "return new XMLHttpRequest()",
        "return process.env",
        "return import('fs')",
    ])
    def test_no_network_or_filesystem(self, code):
        with pytest.raises(ComputeError):
            compute.run_compute(code, {})

    def test_syntax_error_is_compute_error(self):
        with pytest.raises(ComputeError):
            compute.run_compute("return ][", {})

    def test_invalid_ref_variable_name_rejected(self):
        with pytest.raises(ValidationError):
            compute.run_compute("return 1", {"a;delete": [1]})

    def test_objects_and_unicode_roundtrip(self):
        out = compute.run_compute(
            "return {n: rows.length, first: rows[0].label};",
            {"rows": [{"label": "Zoë — größer"}, {"label": "b"}]},
        )
        assert out == {"n": 2, "first": "Zoë — größer"}

    def test_undefined_return_becomes_none(self):
        assert compute.run_compute("const x = 1;", {}) is None


# --- Schreib-Klassifikation -------------------------------------------------------


class TestWriteClassification:
    READ_TOOLS = [
        "welt_constitution", "welt_stats", "welt_vocabulary", "welt_search",
        "welt_resolve", "welt_entities", "welt_entity", "welt_query",
        "welt_timeline", "welt_traverse", "welt_match", "welt_set",
        "welt_path", "welt_common", "welt_rank", "welt_cluster",
        "welt_similar", "welt_changes", "welt_sql", "welt_sources",
        "welt_source", "welt_proposals",
    ]

    @pytest.mark.parametrize("prefix", tools.WRITE_PREFIXES)
    def test_every_prefix_classifies_as_write(self, prefix):
        assert tools.is_write_tool(prefix)
        assert tools.is_write_tool(prefix + "_statements")

    @pytest.mark.parametrize("name", READ_TOOLS)
    def test_read_tools_not_gated(self, name):
        assert not tools.is_write_tool(name)

    def test_registry_split_is_complete(self):
        """Jedes registrierte Tool ist eindeutig read ODER write — und die
        bekannten Schreib-Tools sind alle erfasst."""
        names = {s["function"]["name"] for s in tools.llm_tool_schemas()}
        writes = {n for n in names if tools.is_write_tool(n)}
        reads = names - writes
        assert "welt_commit_statement" in writes
        assert "welt_merge_entities" in writes
        assert "welt_import_snapshot" in writes
        assert "welt_decide_proposal" in writes
        assert set(self.READ_TOOLS) == reads


# --- Schema-Generierung aus der Registry ------------------------------------------


class TestSchemaGeneration:
    def test_every_registered_tool_yields_valid_llm_schema(self):
        schemas = tools.llm_tool_schemas()
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        assert {s["function"]["name"] for s in schemas} == registered
        for schema in schemas:
            assert schema["type"] == "function"
            fn = schema["function"]
            assert fn["name"]
            assert isinstance(fn["description"], str) and fn["description"]
            params = fn["parameters"]
            assert params["type"] == "object"
            assert isinstance(params.get("properties", {}), dict)
            json.dumps(schema)  # serialisierbar fürs LLM-API

    def test_compute_schema_is_separate_local_tool(self):
        names = {s["function"]["name"] for s in tools.llm_tool_schemas()}
        assert "compute" not in names
        assert compute.COMPUTE_TOOL_SCHEMA["function"]["name"] == "compute"


# --- Provider-Auswahl per Env ------------------------------------------------------


class TestProviderSelection:
    def test_default_is_openrouter(self, monkeypatch):
        monkeypatch.delenv("MODEL_PROVIDER", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert isinstance(providers.get_provider(), providers.OpenRouterProvider)

    def test_openrouter_requires_key(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(providers.ProviderError):
            providers.get_provider()

    def test_azure_selected_and_configured(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "azure")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://foo.openai.azure.com/")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azkey")
        provider = providers.get_provider()
        assert isinstance(provider, providers.AzureOpenAIProvider)
        url = provider._url("gpt-4o")
        assert url.startswith(
            "https://foo.openai.azure.com/openai/deployments/gpt-4o/chat/completions"
        )
        assert provider._headers() == {"api-key": "azkey"}
        assert "model" not in provider._body("gpt-4o", [], [])

    def test_azure_requires_endpoint_and_key(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "azure")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        with pytest.raises(providers.ProviderError):
            providers.get_provider()

    def test_unknown_provider_rejected(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "watson")
        with pytest.raises(providers.ProviderError):
            providers.get_provider()

    def test_model_id_env_chain(self, monkeypatch):
        monkeypatch.setenv("MODEL_ID", "explicit/model")
        assert providers.get_model_id() == "explicit/model"
        monkeypatch.delenv("MODEL_ID")
        monkeypatch.setenv("WELTMODELL_LLM_MODEL", "legacy/model")
        assert providers.get_model_id() == "legacy/model"

    def test_ui_models_include_default(self, monkeypatch):
        monkeypatch.setenv("MODEL_ID", "a/base")
        monkeypatch.setenv("WORLDAI_MODELS", "x/one, y/two")
        assert providers.get_ui_models() == ["a/base", "x/one", "y/two"]
