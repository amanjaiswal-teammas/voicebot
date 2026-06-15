from fastapi import FastAPI
from pydantic import BaseModel

from llm import ask_llm
from tts import generate_tts

app = FastAPI()


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest):

    answer = ask_llm(req.message)

    generate_tts(
        answer,
        voice="F1",
        lang="en",
    )

    return {
        "answer": answer,
        "audio": "generated.wav"
    }