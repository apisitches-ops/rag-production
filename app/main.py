import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import check_connection
from app.ingest import ingest_document
from app.query import answer_query

app = FastAPI()


def _normalize_role(value: str | None) -> str | None:
    return value.strip() or None if value else None


class QueryRequest(BaseModel):
    query: str
    acting_role: str | None = None


@app.get("/health")
def health() -> JSONResponse:
    try:
        check_connection()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.post("/documents")
def create_document(file: UploadFile, acl_group: str | None = Form(None)) -> JSONResponse:
    suffix = (Path(file.filename).suffix if file.filename else "") or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp.flush()
        try:
            document_id = ingest_document(
                tmp.name, document_name=file.filename, acl_group=_normalize_role(acl_group)
            )
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "could not ingest file"})
    return JSONResponse(status_code=200, content={"document_id": document_id})


@app.post("/query")
def query(request: QueryRequest) -> JSONResponse:
    try:
        answer = answer_query(request.query, acting_role=_normalize_role(request.acting_role))
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "could not answer query"})
    return JSONResponse(status_code=200, content=answer)
