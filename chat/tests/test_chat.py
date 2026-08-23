"""
Chat comparison API tests.

Production adapters hit real, paid APIs — tests use the module's testing seam
(`providers._testing_stream`) to drive REAL tokens through the full stack
(view -> SSE generator -> persistence). The "no fake fallback" rule applies to
production behavior; a test double injected at the adapter boundary is how the
plumbing gets verified without spending money.
"""
import json
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from chat import providers
from chat.models import ChatSession, ChatTurn, ModelResponse

pytestmark = pytest.mark.django_db

MODELS_URL = "/api/chat/models/"
TURNS_URL = "/api/chat/turns/"


async def _fake_stream(spec, prompt, history, usage, api_key=None):
    """Deterministic stand-in: yields tokens, reports usage like a provider."""
    for token in ["Hel", "lo ", "from ", spec["id"]]:
        yield token
    usage["completion_tokens"] = 4


@pytest.fixture
def seam():
    providers._testing_stream = _fake_stream
    yield
    providers._testing_stream = None


class TestModelsEndpoint:
    def test_requires_auth(self, api):
        assert api.get(MODELS_URL).status_code == 401

    def test_lists_registry_with_connected_flags(self, api_user):
        _, client = api_user
        with patch.dict(
            "os.environ", {"OPENAI_API_KEY": "sk-test", "GROQ_API_KEY": "gsk_test"}
        ):
            body = client.get(MODELS_URL).json()
        ids = {m["id"]: m for m in body}
        assert set(ids) == {s["id"] for s in providers.REGISTRY}
        assert ids["gpt-4o-mini"]["connected"] is True
        assert ids["llama-3.3-70b"]["connected"] is True
        assert ids["claude-3-5-haiku"]["connected"] is False  # honest state
        # keys are never exposed, only booleans
        assert "sk-test" not in json.dumps(body)


class TestTurnCreation:
    def _post(self, client, **body):
        return client.post(TURNS_URL, body, format="json")

    def test_creates_turn_and_pending_responses(self, api_user):
        _, client = api_user
        response = self._post(
            client, prompt="Compare yourselves", model_ids=["gpt-4o-mini", "gemini-2.0-flash"]
        )
        assert response.status_code == 201
        turn_id = response.json()["turn_id"]
        turn = ChatTurn.objects.get(pk=turn_id)
        assert turn.responses.count() == 2
        assert all(r.status == "pending" for r in turn.responses.all())
        assert {r.model_id for r in turn.responses.all()} == {
            "gpt-4o-mini", "gemini-2.0-flash"
        }

    def test_reuses_conversation(self, api_user):
        _, client = api_user
        first = self._post(client, prompt="one", model_ids=["gpt-4o-mini"]).json()
        second = self._post(
            client, prompt="two", model_ids=["gpt-4o-mini"],
            conversation_id=first["conversation_id"],
        ).json()
        assert first["conversation_id"] == second["conversation_id"]
        assert ChatSession.objects.count() == 1
        assert ChatTurn.objects.count() == 2

    def test_rejects_unknown_models(self, api_user):
        _, client = api_user
        response = self._post(client, prompt="x", model_ids=["gpt-99-turbo"])
        assert response.status_code == 400

    def test_rejects_foreign_conversation(self, api_user, django_user_model):
        user, client = api_user
        other = django_user_model.objects.create_user(username="eve", password="x-pass-1234")
        foreign = ChatSession.objects.create(user=other)
        response = self._post(
            client, prompt="x", model_ids=["gpt-4o-mini"], conversation_id=foreign.id
        )
        assert response.status_code == 400

    def test_requires_auth(self, api):
        assert self._post(api, prompt="x", model_ids=["gpt-4o-mini"]).status_code == 401


class TestStreamEndpoint:
    def _turn(self, user, models=("gpt-4o-mini",)):
        session = ChatSession.objects.create(user=user)
        turn = ChatTurn.objects.create(session=session, prompt="hello?")
        for model_id in models:
            spec = providers.get_spec(model_id)
            ModelResponse.objects.create(
                turn=turn, model_id=model_id, provider=spec["provider"],
                model_name=model_id,
            )
        return turn

    def _consume(self, client, turn, model_id):
        """Full-stack: the view returns a SYNC SSE generator, so the test
        client can iterate it directly."""
        response = client.get(f"{TURNS_URL}{turn.id}/stream/{model_id}/")
        assert response.status_code == 200, getattr(response, "content", b"")
        chunks = [chunk.decode() for chunk in response.streaming_content]
        return response, chunks

    def test_streams_tokens_then_done_and_persists(self, api_user, seam):
        user, client = api_user
        turn = self._turn(user)
        response, chunks = self._consume(client, turn, "gpt-4o-mini")
        assert response.status_code == 200
        events = [json.loads(c.removeprefix("data: ").strip()) for c in chunks]
        tokens = [e["token"] for e in events if "token" in e]
        final = [e for e in events if e.get("done")][0]
        assert "".join(tokens) == "Hello from gpt-4o-mini"
        assert final["token_count"] == 4
        assert final["latency_ms"] >= 0
        row = turn.responses.get(model_id="gpt-4o-mini")
        row.refresh_from_db()
        assert row.status == "done"
        assert row.response_text == "Hello from gpt-4o-mini"
        assert row.token_count == 4

    def test_not_connected_streams_error_never_fake(self, api_user):
        user, client = api_user
        turn = self._turn(user, models=("claude-3-5-haiku",))
        _, chunks = self._consume(client, turn, "claude-3-5-haiku")
        events = [json.loads(c.removeprefix("data: ").strip()) for c in chunks]
        assert len(events) == 1
        assert "error" in events[0]
        assert "not connected" in events[0]["error"].lower()
        row = turn.responses.get(model_id="claude-3-5-haiku")
        row.refresh_from_db()
        assert row.status == "error"
        assert "not connected" in row.error_message.lower()
        assert row.response_text == ""  # NO canned content, ever

    def test_provider_failure_surfaces_as_error_event(self, api_user):
        async def exploding(spec, prompt, history, usage, api_key=None):
            raise RuntimeError("HTTP 429: quota exceeded")
            yield  # pragma: no cover

        providers._testing_stream = exploding
        try:
            user, client = api_user
            turn = self._turn(user)
            _, chunks = self._consume(client, turn, "gpt-4o-mini")
            events = [json.loads(c.removeprefix("data: ").strip()) for c in chunks]
            assert "HTTP 429" in events[0]["error"]
            row = turn.responses.get(model_id="gpt-4o-mini")
            row.refresh_from_db()
            assert row.status == "error"
        finally:
            providers._testing_stream = None

    def test_unknown_model_for_turn_is_400(self, api_user):
        user, client = api_user
        turn = self._turn(user)
        response = client.get(f"{TURNS_URL}{turn.id}/stream/gemini-2.0-flash/")
        assert response.status_code == 400
        assert "not part of this turn" in response.json()["detail"]

    def test_history_is_per_model(self, api_user, seam):
        """Column history keeps each model coherent: prior assistant turns
        come from THIS model's own responses only."""
        user, client = api_user
        turn = self._turn(user)
        first, _ = self._consume(client, turn, "gpt-4o-mini")

        # second turn in same session; a claude response exists on turn 1 but
        # the gpt column must not see it as its own words.
        turn2 = ChatTurn.objects.create(session=turn.session, prompt="and now?")
        ModelResponse.objects.create(
            turn=turn2, model_id="gpt-4o-mini", provider="openai",
            model_name="gpt-4o-mini",
        )
        seen_history = {}

        async def spy_stream(spec, prompt, history, usage, api_key=None):
            seen_history.update(history=history, prompt=prompt)
            yield "ok"

        providers._testing_stream = spy_stream
        try:
            self._consume(client, turn2, "gpt-4o-mini")
            assert seen_history["prompt"] == "and now?"
            roles = [m["role"] for m in seen_history["history"]]
            assert roles == ["user", "assistant"]  # turn-1 prompt + gpt's own reply
            assert "gpt-4o-mini" in seen_history["history"][1]["content"]
        finally:
            providers._testing_stream = None


class TestTurnDetail:
    def test_returns_persisted_state(self, api_user, seam):
        user, client = api_user
        turn = ChatTurn.objects.create(
            session=ChatSession.objects.create(user=user), prompt="detail me"
        )
        ModelResponse.objects.create(
            turn=turn, model_id="gpt-4o-mini", provider="openai",
            model_name="gpt-4o-mini", response_text="hi", token_count=1,
            latency_ms=120, status=ModelResponse.Status.DONE,
        )
        body = client.get(f"{TURNS_URL}{turn.id}/").json()
        assert body["prompt"] == "detail me"
        assert body["responses"][0]["response_text"] == "hi"
        assert body["responses"][0]["latency_ms"] == 120

    def test_other_users_turn_is_404(self, api_user, django_user_model):
        user, client = api_user
        other = django_user_model.objects.create_user(username="mallory", password="x-pass-1234")
        foreign_turn = ChatTurn.objects.create(
            session=ChatSession.objects.create(user=other), prompt="secret"
        )
        assert client.get(f"{TURNS_URL}{foreign_turn.id}/").status_code == 404


class TestBYOK:
    """Fix C: bring-your-own-key — encrypted at rest, never echoed, user-aware."""

    KEYS_URL = "/api/chat/keys/"

    def test_post_stores_encrypted_and_flips_connected(self, api_user):
        user, client = api_user
        response = client.post(
            self.KEYS_URL, {"provider": "groq", "api_key": "gsk_my_secret_key_123"},
            format="json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body == {"provider": "groq", "connected": True}  # no key echo

        from chat.models import UserProviderKey
        row = UserProviderKey.objects.get(user=user, provider="groq")
        assert "gsk_my_secret_key_123" not in row.key_ciphertext  # not plaintext
        assert row.key_ciphertext.startswith("gAAAA")             # Fernet envelope

        spec = providers.get_spec("llama-3.3-70b")
        assert not providers.is_connected(spec)                    # global: no
        assert providers.is_connected_for(spec, user)              # BYOK: yes

    def test_models_endpoint_is_user_aware(self, api_user, api):
        user, client = api_user
        before = {m["id"]: m["connected"] for m in client.get(MODELS_URL).json()}
        assert before["llama-3.1-8b-instant"] is False
        client.post(self.KEYS_URL, {"provider": "groq", "api_key": "gsk_user_key"}, format="json")
        after = {m["id"]: m["connected"] for m in client.get(MODELS_URL).json()}
        assert after["llama-3.3-70b"] is True
        assert after["llama-3.1-8b-instant"] is True               # same provider key
        assert after["gemma2-9b-it"] is True
        assert after["gpt-4o-mini"] is False                       # other providers unaffected

        # other users see no change (a second, key-less authenticated user)
        from django.contrib.auth import get_user_model
        stranger = get_user_model().objects.create_user(username="stranger", password="x-pass-1234")
        stranger_client = APIClient()
        stranger_client.force_authenticate(user=stranger)
        other = {m["id"]: m["connected"] for m in stranger_client.get(MODELS_URL).json()}
        assert other["llama-3.3-70b"] is False

    def test_stream_uses_the_user_key_not_global_env(self, api_user):
        user, client = api_user
        client.post(self.KEYS_URL, {"provider": "groq", "api_key": "gsk_stream_key"}, format="json")

        captured = {}

        async def spy(spec, prompt, history, usage, api_key=None):
            captured["api_key"] = api_key
            yield "ok"

        providers._testing_stream = spy
        try:
            session = ChatSession.objects.create(user=user)
            turn = ChatTurn.objects.create(session=session, prompt="hi")
            ModelResponse.objects.create(
                turn=turn, model_id="llama-3.3-70b", provider="groq",
                model_name="llama-3.3-70b-versatile",
            )
            response = client.get(f"{TURNS_URL}{turn.id}/stream/llama-3.3-70b/")
            assert response.status_code == 200
            chunks = [c.decode() for c in response.streaming_content]
            assert any('"token": "ok"' in c for c in chunks)
            assert captured["api_key"] == "gsk_stream_key"          # BYOK flows to the adapter
        finally:
            providers._testing_stream = None

    def test_delete_removes_key(self, api_user):
        user, client = api_user
        client.post(self.KEYS_URL, {"provider": "gemini", "api_key": "AIza_user_key"}, format="json")
        gone = client.delete(f"{self.KEYS_URL}gemini/")
        assert gone.status_code == 200
        assert gone.json() == {"provider": "gemini", "deleted": True, "connected": False}
        spec = providers.get_spec("gemini-2.0-flash")
        assert not providers.is_connected_for(spec, user)

    def test_unknown_provider_rejected(self, api_user):
        _, client = api_user
        response = client.post(
            self.KEYS_URL, {"provider": "skynet", "api_key": "x" * 20}, format="json"
        )
        assert response.status_code == 400

    def test_requires_auth(self, api):
        assert api.post(self.KEYS_URL, {"provider": "groq", "api_key": "k" * 20}, format="json").status_code == 401

    def test_roundtrip_decrypt(self, api_user):
        from chat.crypto import decrypt_secret, encrypt_secret
        ciphertext = encrypt_secret("gsk_roundtrip")
        assert decrypt_secret(ciphertext) == "gsk_roundtrip"
