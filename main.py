from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine import answer

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.post("/")
@app.post("/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        question = str(body.get("question", ""))
    except Exception:
        return JSONResponse({"error": "send JSON like {\"question\": \"...\"}"}, status_code=400)
    result, _ = answer(question)
    return {"answer": result}


@app.get("/")
async def health():
    return {"status": "ok", "usage": "POST {\"question\": \"...\"}"}
