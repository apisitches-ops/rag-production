import shutil
import tempfile
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import check_connection
from app.ingest import ingest_document
from app.query import answer_query
from app.reranker import warm_up


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load the reranker model at startup, not on the first real request —
    # otherwise the first /query call blocks on a multi-GB download.
    warm_up()
    yield


app = FastAPI(lifespan=lifespan)


class QueryRequest(BaseModel):
    query: str


@app.get("/health")
def health() -> JSONResponse:
    try:
        check_connection()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.post("/documents")
def create_document(file: UploadFile) -> JSONResponse:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp.flush()
        try:
            document_id = ingest_document(tmp.name, document_name=file.filename)
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "could not ingest file"})
    return JSONResponse(status_code=200, content={"document_id": document_id})


@app.post("/query")
def query(request: QueryRequest) -> JSONResponse:
    try:
        answer = answer_query(request.query)
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "could not answer query"})
    return JSONResponse(status_code=200, content=answer)
