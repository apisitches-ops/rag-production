import shutil
import tempfile

from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse

from app.db import check_connection
from app.ingest import ingest_document

app = FastAPI()


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
