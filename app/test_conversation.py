from conversation import process_call

result = process_call(
    call_id="CALL001",
    audio_file="sample.wav"
)

print(result)