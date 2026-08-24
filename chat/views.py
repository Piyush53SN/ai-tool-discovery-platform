"""
Chat comparison API (Section 4.2).

    GET  /api/chat/models/                     registry + connected booleans
    POST /api/chat/turns/                      fan a prompt out to N models
    GET  /api/chat/turns/{id}/stream/{model}/  per-model SSE stream
    GET  /api/chat/turns/{id}/                 turn + persisted responses

SSE protocol (per spec): `data: {"token": "..."}` repeatedly, then
`data: {"done": true, "usage": {...}}`, or `data: {"error": "..."}` on
failure. One SSE connection per model — a slow or erroring provider never
affects its siblings.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time

from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import providers
from .models import ChatSession, ChatTurn, ModelResponse
from .serializers import (
    ChatModelsSerializer,
    ProviderKeySerializer,
    TurnCreateSerializer,
    TurnDetailSerializer,
)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


class ChatModelsView(APIView):
    """GET /api/chat/models/ — what can be compared right now, honestly."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # `connected` is user-aware: global env key OR this user's BYOK key.
        return Response(
            ChatModelsSerializer(providers.public_models(request.user), many=True).data
        )


class TurnView(APIView):
    """POST /api/chat/turns/ — the cost-gated fan-out entry point."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat"  # directly controls API spend exposure

    def post(self, request):
        serializer = TurnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prompt = serializer.validated_data["prompt"]
        model_ids = serializer.validated_data["model_ids"]
        conversation_id = serializer.validated_data.get("conversation_id")

        session = None
        if conversation_id:
            session = ChatSession.objects.filter(
                pk=conversation_id, user=request.user
            ).first()
            if session is None:
                return Response(
                    {"conversation_id": "Unknown conversation for this user."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if session is None:
            session = ChatSession.objects.create(user=request.user, title=prompt[:80])

        turn = ChatTurn.objects.create(session=session, prompt=prompt)
        for model_id in model_ids:
            spec = providers.get_spec(model_id)
            ModelResponse.objects.create(
                turn=turn,
                model_id=model_id,
                provider=spec["provider"],
                model_name=providers.vendor_model(spec),
                status=ModelResponse.Status.PENDING,
            )

        return Response(
            {"turn_id": str(turn.id), "conversation_id": str(session.id)},
            status=status.HTTP_201_CREATED,
        )


class TurnDetailView(APIView):
    """GET /api/chat/turns/{id}/ — persisted state (history/reload)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, turn_id: str):
        turn = ChatTurn.objects.filter(
            pk=turn_id, session__user=request.user
        ).prefetch_related("responses").first()
        if turn is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(TurnDetailSerializer(turn).data)


class TurnStreamView(APIView):
    """GET /api/chat/turns/{id}/stream/{model_id}/ — one model, one SSE pipe.

    The provider adapters are async; this view bridges them into a SYNC SSE
    generator (thread + queue + dedicated event loop), which streams reliably
    under the WSGI dev server, the test client and ASGI alike. One SSE
    connection per model — a slow or erroring provider never blocks siblings.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, turn_id: str, model_id: str):
        owns_turn = ChatTurn.objects.filter(
            pk=turn_id, session__user=request.user
        ).exists()
        if not owns_turn:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        response_row = ModelResponse.objects.filter(
            turn_id=turn_id, model_id=model_id
        ).first()
        if response_row is None:
            return Response(
                {"detail": f"Model '{model_id}' is not part of this turn."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        spec = providers.get_spec(model_id)
        connected = spec is not None and providers.is_reachable(spec, request.user)
        api_key = providers.get_api_key(spec, request.user) if spec else None
        prompt = ChatTurn.objects.get(pk=turn_id).prompt

        def event_stream():
            started = time.monotonic()
            text_parts: list[str] = []
            usage: dict = {}
            final_status = ModelResponse.Status.DONE
            error_message = ""

            def persist():
                text = "".join(text_parts)
                ModelResponse.objects.filter(pk=response_row.pk).update(
                    response_text=text,
                    token_count=(
                        usage.get("completion_tokens")
                        or usage.get("output_tokens")
                        or providers.estimate_tokens(text)
                    ),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status=final_status,
                    error_message=error_message[:1000],
                )

            def pump(worker: queue.Queue):
                """Run the adapter's event loop in this thread, feeding the queue."""
                async def main():
                    async for token in providers.stream_chat(spec, prompt, history, usage, api_key=api_key):
                        q.put(("token", token))
                    q.put(("done", None))

                try:
                    asyncio.run(main())
                except Exception as exc:
                    q.put(("error", f"{type(exc).__name__}: {exc}"[:300]))
                finally:
                    q.put(None)

            try:
                if not connected:
                    # Honest state, never a fake response (Section 4.4).
                    final_status = ModelResponse.Status.ERROR
                    error_message = "Provider not connected (missing API key)."
                    yield sse({"error": error_message})
                    return

                ModelResponse.objects.filter(pk=response_row.pk).update(
                    status=ModelResponse.Status.STREAMING
                )
                history = self._history_for(turn_id, model_id)

                q: queue.Queue = queue.Queue()
                loop_holder = threading.Thread(target=pump, args=(q,), daemon=True)
                loop_holder.start()

                deadline = time.monotonic() + providers.REQUEST_TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        final_status = ModelResponse.Status.ERROR
                        error_message = (
                            f"Timed out after {providers.REQUEST_TIMEOUT:.0f}s."
                        )
                        yield sse({"error": error_message})
                        break
                    try:
                        item = q.get(timeout=min(remaining, 0.5))
                    except queue.Empty:
                        continue
                    if item is None:
                        break
                    kind, payload = item
                    if kind == "token":
                        text_parts.append(payload)
                        yield sse({"token": payload})
                    elif kind == "error":
                        final_status = ModelResponse.Status.ERROR
                        error_message = payload
                        yield sse({"error": payload})
                        break
                    elif kind == "done":
                        text = "".join(text_parts)
                        yield sse({
                            "done": True,
                            "usage": usage or None,
                            "token_count": (
                                usage.get("completion_tokens")
                                or usage.get("output_tokens")
                                or providers.estimate_tokens(text)
                            ),
                            "latency_ms": int((time.monotonic() - started) * 1000),
                        })
                        break
            except GeneratorExit:
                # client disconnected mid-stream — keep what we streamed
                final_status = ModelResponse.Status.ERROR
                error_message = error_message or "client disconnected"
                raise
            finally:
                persist()

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    @staticmethod
    def _history_for(turn_id: str, model_id: str) -> list[dict]:
        """This model's own prior answers in the same session — each column
        keeps a coherent per-model conversation."""
        turn = ChatTurn.objects.prefetch_related("responses").get(pk=turn_id)
        history: list[dict] = []
        for prior in turn.session.turns.order_by("created_at").prefetch_related("responses"):
            if prior.pk == turn.pk:
                break
            history.append({"role": "user", "content": prior.prompt})
            reply = next(
                (r for r in prior.responses.all()
                 if r.model_id == model_id and r.status == ModelResponse.Status.DONE),
                None,
            )
            if reply:
                history.append({"role": "assistant", "content": reply.response_text})
        return history



class ProviderKeyView(APIView):
    """BYOK (Fix C): store/remove the signed-in user's own provider key.

    POST   /api/chat/keys/   {provider, api_key}  -> {provider, connected}
    DELETE /api/chat/keys/{provider}/             -> {provider, connected}

    Keys are Fernet-encrypted at rest (chat/crypto.py) and never echoed.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "writes"

    def post(self, request):
        serializer = ProviderKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data["provider"]
        api_key = serializer.validated_data["api_key"].strip()
        if len(api_key) < 8:
            return Response(
                {"api_key": "That does not look like an API key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # One cheap REAL call before saving: a rejected key must fail HERE,
        # not mid-chat (BYOK Fix 1). Nothing is stored on failure.
        spec = next(s for s in providers.REGISTRY if s["provider"] == provider)
        ok, message = providers.validate_key(spec, api_key)
        if not ok:
            return Response({"api_key": message}, status=status.HTTP_400_BAD_REQUEST)

        from .crypto import encrypt_secret
        from .models import UserProviderKey

        UserProviderKey.objects.update_or_create(
            user=request.user,
            provider=provider,
            defaults={"key_ciphertext": encrypt_secret(api_key)},
        )
        spec = next(s for s in providers.REGISTRY if s["provider"] == provider)
        return Response({"provider": provider, "connected": providers.is_connected_for(spec, request.user)})

    def delete(self, request, provider: str):
        from .models import UserProviderKey

        deleted, _ = UserProviderKey.objects.filter(
            user=request.user, provider=provider
        ).delete()
        spec = next((s for s in providers.REGISTRY if s["provider"] == provider), None)
        connected = (
            providers.is_connected_for(spec, request.user) if spec else False
        )
        return Response(
            {"provider": provider, "deleted": bool(deleted), "connected": connected}
        )
