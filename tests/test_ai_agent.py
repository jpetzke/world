"""Agent-Loop mit gemocktem LLM: deterministische Tool-Call-Sequenzen gegen
einen Fixture-Graph — prüft das Zusammenspiel von Offloading, compute,
Anker-Cache, Schreib-Gate und finaler Antwort."""

import json
import uuid

import anyio
import pytest

from weltmodell.ai import providers, sessions
from weltmodell.ai.agent import MAX_ITERATIONS, AgentTurn
from weltmodell.ai.providers import ChatProvider
from weltmodell.db import get_conn
from weltmodell.entities import create_entity
from weltmodell.pipeline import ingest_document
from weltmodell.statements import commit_statement


# --- Mock-LLM ---------------------------------------------------------------------


class MockProvider(ChatProvider):
    """Spielt ein festes Skript ab. Ein Step ist eine Assistant-Message oder
    eine Funktion history→Message (für Werte, die erst zur Laufzeit
    existieren, z. B. ref:<id> aus dem Result-Store)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    async def stream_chat(self, messages, tools, model):
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        assert self.steps, "Mock-Skript erschöpft — Loop rief öfter als geplant"
        step = self.steps.pop(0)
        message = step(messages) if callable(step) else step
        if message.get("content"):
            for word in message["content"].split(" "):
                yield {"type": "text", "delta": word + " "}
        yield {"type": "message", "message": message,
               "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}


def _call(name, arguments, call_id=None):
    return {
        "id": call_id or f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {"name": name,
                     "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def _assistant(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def run_turn(session_id, **kwargs):
    async def go():
        return [e async for e in AgentTurn(session_id).run(**kwargs)]

    return anyio.run(go)


def events_of(events, name):
    return [e["data"] for e in events if e["event"] == name]


@pytest.fixture
def mock_llm(monkeypatch):
    holder = {}

    def install(steps):
        provider = MockProvider(steps)
        monkeypatch.setattr(providers, "get_provider", lambda: provider)
        holder["provider"] = provider
        return provider

    return install


@pytest.fixture
def session_id(database):
    # Eigene, sofort committende Verbindung — der Agent-Loop öffnet pro
    # Persistenz-Schritt frische Verbindungen und muss die Session sehen.
    with get_conn() as c:
        return str(sessions.create_session(c)["id"])


# --- Fixture-Graph: zwei Accounts mit überlappenden Followern -----------------------


@pytest.fixture(scope="module")
def follow_graph(database):
    with get_conn() as c:
        src = str(ingest_document(
            c, raw={"fixture": "ai-agent"}, url="https://example.org/ai-fx",
            activity="test:ai", agent="pytest",
        )["id"])
        g = {"src": src}
        for key, label in [("acc_a", "@ai_alice"), ("acc_b", "@ai_bob")]:
            g[key] = str(create_entity(
                c, type_id="SocialMediaAccount", label=label)["id"])
        for i in range(1, 7):
            g[f"f{i}"] = str(create_entity(
                c, type_id="SocialMediaAccount", label=f"@ai_follower{i}")["id"])
        # acc_a ← f1..f4 · acc_b ← f3..f6 → Schnittmenge {f3, f4}
        for f in ("f1", "f2", "f3", "f4"):
            commit_statement(c, subject_id=g[f], predicate_id="follows",
                             value={"type": "entity", "object_id": g["acc_a"]},
                             source_ids=[src])
        for f in ("f3", "f4", "f5", "f6"):
            commit_statement(c, subject_id=g[f], predicate_id="follows",
                             value={"type": "entity", "object_id": g["acc_b"]},
                             source_ids=[src])
    return g


# --- „wer folgt A und B": kompletter Loop mit Offloading + compute -------------------


def test_who_follows_both_full_loop(follow_graph, session_id, mock_llm, monkeypatch):
    monkeypatch.setenv("WORLDAI_RESULT_THRESHOLD", "400")
    g = follow_graph

    def compute_step(messages):
        """Refs der beiden offgeloadeten welt_query-Ergebnisse einsammeln."""
        refs = []
        for m in messages:
            if m.get("role") == "tool":
                content = json.loads(m["content"])
                if isinstance(content, dict) and content.get("ref"):
                    refs.append(content["ref"])
        assert len(refs) == 2, f"erwartete 2 Refs, fand {refs}"
        return _assistant(tool_calls=[_call("compute", {
            "code": ("const sa = new Set(a.statements.map(s => s.subject_id));"
                     "return b.statements.map(s => s.subject_id)"
                     ".filter(x => sa.has(x));"),
            "refs": {"a": refs[0], "b": refs[1]},
        }, call_id="call_compute")])

    provider = mock_llm([
        _assistant(tool_calls=[
            _call("welt_resolve",
                  {"type_id": "SocialMediaAccount", "label": "@ai_alice"}),
            _call("welt_resolve",
                  {"type_id": "SocialMediaAccount", "label": "@ai_bob"}),
        ]),
        _assistant(tool_calls=[
            _call("welt_query", {"predicate_id": "follows",
                                 "object_id": g["acc_a"], "output": "compact",
                                 "limit": 100}),
            _call("welt_query", {"predicate_id": "follows",
                                 "object_id": g["acc_b"], "output": "compact",
                                 "limit": 100}),
        ]),
        compute_step,
        _assistant(content="Beide Accounts teilen sich zwei Follower."),
    ])

    events = run_turn(session_id, user_text="Wer folgt @ai_alice und @ai_bob?")

    # Kein Fehler, sauberer Abschluss.
    assert events_of(events, "error") == []
    assert events_of(events, "done") == [{"reason": "final"}]

    # Tool-Sequenz sichtbar gestreamt.
    starts = [d["name"] for d in events_of(events, "tool_start")]
    assert starts == ["welt_resolve", "welt_resolve",
                      "welt_query", "welt_query", "compute"]

    # Offloading: beide welt_query-Ergebnisse gingen in den Result-Store.
    query_results = [d for d in events_of(events, "tool_result")
                     if d["name"] == "welt_query"]
    assert all(d["offloaded"] and d["ref"].startswith("ref:")
               for d in query_results)
    # … und das Modell sah Summary + Sample statt Volldaten.
    tool_msgs = [m for m in provider.calls[2]["messages"] if m["role"] == "tool"]
    digest = json.loads(tool_msgs[-1]["content"])
    assert digest["offloaded"] and "sample" in digest and "summary" in digest

    # compute lieferte exakt die Schnittmenge {f3, f4}.
    compute_result = next(d for d in events_of(events, "tool_result")
                          if d["name"] == "compute")
    assert not compute_result["offloaded"]
    assert set(compute_result["display"]) == {g["f3"], g["f4"]}

    # Finale Antwort gestreamt und persistiert.
    assert "".join(d["text"] for d in events_of(events, "token")).strip() \
        == "Beide Accounts teilen sich zwei Follower."
    with get_conn() as c:
        stored = sessions.get_session(c, session_id)
    final = stored["messages"][-1]["payload"]
    assert final["role"] == "assistant"
    assert final["content"] == "Beide Accounts teilen sich zwei Follower."

    # Anker-Cache: beide resolve-Treffer gesammelt, noch nicht gesendet.
    anchor_ids = {a["id"] for a in stored["anchors"]}
    assert anchor_ids == {g["acc_a"], g["acc_b"]}

    # LLM sah alle Tool-Schemas aus der Registry + compute.
    tool_names = {t["function"]["name"] for t in provider.calls[0]["tools"]}
    assert "welt_query" in tool_names and "compute" in tool_names

    # System-Prompt ist statischer Prefix an Position 0.
    assert provider.calls[0]["messages"][0]["role"] == "system"
    assert "Verfassung" in provider.calls[0]["messages"][0]["content"] \
        or "VERFASSUNG" in provider.calls[0]["messages"][0]["content"]


def test_anchor_block_prepended_on_next_turn(follow_graph, session_id, mock_llm):
    g = follow_graph
    mock_llm([
        _assistant(tool_calls=[_call(
            "welt_resolve",
            {"type_id": "SocialMediaAccount", "label": "@ai_alice"})]),
        _assistant(content="Gefunden."),
    ])
    run_turn(session_id, user_text="Finde @ai_alice")

    provider2 = MockProvider([_assistant(content="Ok.")])
    import weltmodell.ai.providers as prov_mod
    original = prov_mod.get_provider
    prov_mod.get_provider = lambda: provider2
    try:
        events = run_turn(session_id, user_text="Und weiter?")
    finally:
        prov_mod.get_provider = original
    assert events_of(events, "error") == []

    history = provider2.calls[0]["messages"]
    anchor_msgs = [m for m in history
                   if m["role"] == "user" and "Anker-Cache" in (m["content"] or "")]
    assert len(anchor_msgs) == 1
    assert g["acc_a"] in anchor_msgs[0]["content"]
    # Anker-Block steht VOR der neuen User-Nachricht.
    assert history.index(anchor_msgs[0]) \
        < history.index(next(m for m in history
                             if m.get("content") == "Und weiter?"))


def test_iteration_limit_breaks_cleanly(session_id, mock_llm):
    steps = [
        _assistant(tool_calls=[_call("welt_stats", {})])
        for _ in range(MAX_ITERATIONS)
    ]
    steps.append(_assistant(content="Zwischenstand: 20 Runden Statistik."))
    provider = mock_llm(steps)

    events = run_turn(session_id, user_text="Lauf im Kreis")
    assert events_of(events, "done") == [{"reason": "max_iterations"}]
    # Abschlussrunde lief ohne Tools.
    assert provider.calls[-1]["tools"] == []
    assert provider.steps == []


def test_tool_error_feeds_back_to_model(session_id, mock_llm):
    mock_llm([
        _assistant(tool_calls=[_call("welt_entity",
                                     {"entity_id": str(uuid.uuid4())})]),
        _assistant(content="Die Entity existiert nicht."),
    ])
    events = run_turn(session_id, user_text="Zeig mir die Entity")
    result = events_of(events, "tool_result")[0]
    assert "error" in result
    assert events_of(events, "done") == [{"reason": "final"}]


# --- Schreib-Gate ---------------------------------------------------------------------


def _commit_call(g, call_id="call_write"):
    return _call("welt_commit_statement", {
        "subject_id": g["f1"], "predicate_id": "follows",
        "value": {"type": "entity", "object_id": g["acc_b"]},
        "source_ids": [g["src"]],
    }, call_id=call_id)


def _count_f1_follows_b(g):
    with get_conn() as c:
        return c.execute(
            """SELECT count(*) AS n FROM statement
               WHERE subject_id = %s AND predicate_id = 'follows'
                 AND object_id = %s AND system_to IS NULL""",
            (g["f1"], g["acc_b"]),
        ).fetchone()["n"]


def test_write_tool_pauses_without_confirmation(follow_graph, session_id, mock_llm):
    g = follow_graph
    before = _count_f1_follows_b(g)
    mock_llm([_assistant(content="Ich lege das an.",
                         tool_calls=[_commit_call(g)])])

    events = run_turn(session_id, user_text="f1 folgt jetzt auch acc_b")

    confirms = events_of(events, "confirm_required")
    assert len(confirms) == 1
    assert confirms[0]["name"] == "welt_commit_statement"
    assert confirms[0]["arguments"]["subject_id"] == g["f1"]
    assert events_of(events, "done") == [{"reason": "confirm"}]
    # NICHT ausgeführt.
    assert _count_f1_follows_b(g) == before
    # Pending persistiert (UI kann die Karte nach Reload wieder rendern).
    with get_conn() as c:
        stored = sessions.get_session(c, session_id)
    assert stored["pending"]["tool_call_id"] == "call_write"


def test_rejection_returns_to_model_without_executing(follow_graph, session_id,
                                                      mock_llm):
    g = follow_graph
    before = _count_f1_follows_b(g)
    mock_llm([
        _assistant(tool_calls=[_commit_call(g)]),
        _assistant(content="Verstanden, ich schreibe nichts."),
    ])
    run_turn(session_id, user_text="f1 folgt jetzt auch acc_b")

    events = run_turn(session_id,
                      confirm={"tool_call_id": "call_write", "approved": False})
    rejected = [d for d in events_of(events, "tool_result") if d.get("rejected")]
    assert len(rejected) == 1
    assert events_of(events, "done") == [{"reason": "final"}]
    assert _count_f1_follows_b(g) == before
    with get_conn() as c:
        stored = sessions.get_session(c, session_id)
    assert stored["pending"] is None
    # Das Modell sah die Ablehnung als Tool-Result.
    tool_msg = next(m["payload"] for m in stored["messages"]
                    if m["payload"].get("role") == "tool")
    assert "ABGELEHNT" in tool_msg["content"]


def test_approval_executes_but_constitution_lock_still_applies(
        follow_graph, session_id, mock_llm):
    """Bestätigter Write ohne vorherigen welt_constitution-Call scheitert am
    Server-Gate — der Session-Lock gilt unverändert für WorldAI."""
    g = follow_graph
    before = _count_f1_follows_b(g)
    mock_llm([
        _assistant(tool_calls=[_commit_call(g)]),
        _assistant(content="Das Gate verlangt erst die Verfassung."),
    ])
    run_turn(session_id, user_text="f1 folgt jetzt auch acc_b")

    events = run_turn(session_id,
                      confirm={"tool_call_id": "call_write", "approved": True})
    result = events_of(events, "tool_result")[0]
    assert "Schreibaktion gesperrt" in result["error"]
    assert _count_f1_follows_b(g) == before


def test_approval_after_constitution_ack_executes(follow_graph, session_id,
                                                  mock_llm):
    """Vollpfad: welt_constitution → Write → Bestätigung → Statement steht."""
    g = follow_graph
    before = _count_f1_follows_b(g)
    mock_llm([
        _assistant(tool_calls=[_call("welt_constitution", {},
                                     call_id="call_verf")]),
        _assistant(tool_calls=[_commit_call(g)]),
        _assistant(content="Committed."),
    ])
    events = run_turn(session_id, user_text="f1 folgt jetzt auch acc_b")
    assert events_of(events, "confirm_required")[0]["name"] \
        == "welt_commit_statement"
    assert _count_f1_follows_b(g) == before  # noch nichts geschrieben

    events = run_turn(session_id,
                      confirm={"tool_call_id": "call_write", "approved": True})
    assert events_of(events, "error") == []
    result = events_of(events, "tool_result")[0]
    assert "error" not in result
    assert _count_f1_follows_b(g) == before + 1
    assert events_of(events, "done") == [{"reason": "final"}]


def test_stale_confirm_is_refused(session_id, mock_llm):
    mock_llm([])
    events = run_turn(session_id,
                      confirm={"tool_call_id": "gibtsnicht", "approved": True})
    assert "Keine passende Bestätigung" in events_of(events, "error")[0]["message"]
