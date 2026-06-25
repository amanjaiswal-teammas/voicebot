from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from .session import create_session
from .conversation import process_call
import os
from fastapi import Form

app = FastAPI()


@app.post("/voice-audio")
async def voice_audio(
    audio: UploadFile = File(...),
    call_id: str = Form(None)
):
    
    if not call_id:
        call_id = create_session()

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