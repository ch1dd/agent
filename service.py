"""Loopback API for authenticated PHP handlers. Run python service.py."""
import hmac
import os
from contextlib import asynccontextmanager
from tempfile import SpooledTemporaryFile
from typing import Literal
from uuid import UUID

import uvicorn
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from config import Config
from consultant import Consultant
from ollama_api import ModelService
from storage import Store


class Previous(BaseModel):
    request: str | None = None
    depends_on: list[str] | None = None
    question: str = Field(max_length=2000)
    text: str = Field(max_length=12000)
    kind: Literal["answer", "clarify", "unknown", "off_topic"]
    human_answer: str | None = Field(default=None, max_length=1500)


class Ask(BaseModel):
    owner: str = Field(min_length=1, max_length=128)
    chat: str = Field(min_length=1, max_length=128)
    request: UUID
    question: str = Field(min_length=1, max_length=2000)
    previous: list[Previous] | None = Field(default=None, max_length=64)


class Review(BaseModel):
    revision: int = Field(ge=0)
    topic: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=1500)
    reviewer: str = Field(min_length=1, max_length=120)
    publish: bool


async def ask(request):
    data = Ask.model_validate(await request.json()).model_dump(mode="json")
    result = await run_in_threadpool(request.app.state.bot.ask, **data)
    return JSONResponse(result)


async def original(request):
    name, path = request.app.state.bot.original(request.path_params["version"])
    return FileResponse(path, filename=name, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store"})


async def ticket(request):
    with request.app.state.bot.store.db() as conn:
        row = conn.execute("""SELECT id,topic,answer,reviewer,resolved,publication,revision FROM tickets
                            WHERE id=? AND owner=?""",
                           (request.path_params["number"], request.query_params.get("owner"))).fetchone()
    return JSONResponse(dict(row) if row else {"error": "Заявка не найдена."}, status_code=200 if row else 404)


async def tickets(request):
    return JSONResponse(await run_in_threadpool(request.app.state.bot.store.tickets))


async def review(request):
    data = Review.model_validate(await request.json())
    bot = request.app.state.bot
    job = await run_in_threadpool(bot.store.review, request.path_params["number"], **data.model_dump())
    bot.wake.set()
    return JSONResponse({"saved": True, "job_id": job})


async def upload(request):
    name = request.query_params.get("name", "")
    with SpooledTemporaryFile(max_size=2**20) as stream:
        async for block in request.stream():
            if stream.tell() + len(block) > 64 * 2**20:
                return JSONResponse({"error": "Максимальный размер файла 64 МиБ."}, status_code=413)
            stream.write(block)
        stream.seek(0)
        job = await run_in_threadpool(request.app.state.bot.upload, name, stream,
                                      request.query_params.get("owner", ""), request.query_params.get("request", ""))
    return JSONResponse({"job_id": job}, status_code=202)


async def jobs(request):
    return JSONResponse(await run_in_threadpool(request.app.state.bot.jobs))


class PublicationLink(BaseModel):
    ticket: int = Field(gt=0)
    user: int = Field(gt=0)
    request: str


class Withdraw(BaseModel):
    source: str
    links: list[PublicationLink] = Field(default_factory=list)


async def documents(request):
    from withdrawal import sources
    bot = request.app.state.bot
    if request.method == "GET":
        return JSONResponse(await run_in_threadpool(sources, bot.store))
    data = Withdraw.model_validate(await request.json())
    links = [(f"upload:faq_ticket_{r.ticket}.txt", f"site:{r.user}", r.request) for r in data.links]
    await run_in_threadpool(bot.withdraw, data.source, links)
    return JSONResponse({"removed": True})


async def removed_answers(request):
    with request.app.state.bot.store.db() as conn:
        rows = conn.execute("""SELECT owner,request FROM turns WHERE owner LIKE 'site:%'
            AND json_extract(result,'$.withdrawn')=1""").fetchall()
    return JSONResponse([dict(r) for r in rows])


def create_app(bot=None):
    user_key = os.environ.get("RAG_API_TOKEN", "")
    admin_key = os.environ.get("RAG_REVIEW_TOKEN", "")
    if not user_key or not admin_key or user_key == admin_key:
        raise ValueError("Задайте разные RAG_API_TOKEN и RAG_REVIEW_TOKEN.")

    @asynccontextmanager
    async def lifespan(app):
        config = Config()
        app.state.bot = bot or Consultant(Store(config, ModelService(config)))
        try:
            yield
        finally:
            if bot is None:
                await run_in_threadpool(app.state.bot.close)

    routes = [Route("/ask", ask, methods=["POST"]), Route("/originals/{version}", original),
              Route("/tickets/{number:int}", ticket), Route("/admin/tickets", tickets),
              Route("/admin/tickets/{number:int}", review, methods=["POST"]),
              Route("/admin/documents", documents, methods=["GET","POST"]),
              Route("/admin/removed-answers", removed_answers),
              Route("/admin/uploads", upload, methods=["POST"]), Route("/admin/jobs", jobs)]
    app = Starlette(routes=routes, lifespan=lifespan)

    async def authorize(request, call_next):
        key = admin_key if request.url.path.startswith("/admin/") else user_key
        if not hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {key}".encode()):
            return JSONResponse({"error": "Нет доступа."}, status_code=403)
        try:
            return await call_next(request)
        except (ValidationError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    app.add_middleware(BaseHTTPMiddleware, dispatch=authorize)
    return app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.getenv("RAG_API_PORT", "8765")))
