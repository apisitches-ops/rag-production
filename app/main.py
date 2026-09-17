from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.db import check_connection

app = FastAPI()


@app.get("/health")
def health() -> JSONResponse:
    try:
        check_connection()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})
    return JSONResponse(status_code=200, content={"status": "ok"})
