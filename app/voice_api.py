from typing import Optional
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from .session import create_session
from .conversation import process_call
from .memory import add_message
import os
from fastapi import Form, HTTPException

app = FastAPI()


@app.post("/voice-audio")
async def voice_audio(
    audio: Optional[UploadFile] = File(None),
    call_id: str = Form(None),
    outbound: bool = Form(False),
):
    
    if not call_id:
        call_id = create_session()

    if outbound:
        output = f"audio/{call_id}_welcome.wav"

        from .supertonic_engine import speak

        greeting = (
            "Good morning, sir. My name is Aman and I am calling from BellaVita. "
            "Am I speaking with Mr Prakhar? "
            "Sir, as I can check you have added a product in your cart on our Bellavita's Website. "
            "Firstly, I really want to appreciate your choice. "
            "I noticed you haven't placed the order yet. "
            "We are currently offering the best exclusive discount on this product. "
            "May I confirm the order on your behalf?"
        )

        speak(greeting, output, "en")

        add_message(call_id, "assistant", greeting)

        return FileResponse(
            output,
            media_type="audio/wav",
            filename="welcome.wav",
        )
    
    if audio is None:
        raise HTTPException(
            status_code=400,
            detail="Audio file missing"
        )

    temp_file = f"{call_id}.wav"

    with open(temp_file, "wb") as f:
        f.write(await audio.read())

    print("UPLOAD FILE:", temp_file)
    print("UPLOAD SIZE:", os.path.getsize(temp_file))

    result = process_call(
        call_id,
        temp_file
    )

    return FileResponse(
        path=result["audio"],
        media_type="audio/wav",
        filename="response.wav"
    )