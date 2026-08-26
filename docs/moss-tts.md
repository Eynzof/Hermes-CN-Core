# Moss (mosi.cn) TTS — Usage

Moss is a bundled TTS provider plugin at `plugins/tts/moss/`. It provides
single-voice `text_to_speech`, streaming, multi-speaker dialogue, voice
design, and voice cloning through the standard Hermes TTS surface.

---

## Setup

### 1. API key (one of, in resolution order)

1. `tts.moss.api_key` in `config.yaml`
2. `MOSS_API_KEY` in env / `~/.hermes/.env` (profile secret scope)
3. `hermes auth add moss` (credential pool)
4. A key file configured via `MOSS_KEY_FILE` env var (client fallback —
   the file may contain `{"api_key": "..."}` or a raw single-token key)

```bash
# preferred
hermes auth add moss
# or .env
MOSS_API_KEY=sk-...
# or a key file
MOSS_KEY_FILE=/secure/path/moss-key.txt
```

### 2. config.yaml

```yaml
tts:
  provider: "moss"          # pin Moss as the TTS backend
  moss:
    # api_key: ""           # optional; see above
    model: "moss-tts"       # classic alias — /v1/audio/speech rejects new model
                            # IDs when a separate version field is sent
    version: "flash-20260626"
    voice_id: "94aa4989-c7e9-5007-ae42-ab401823e6c9"
    delivery_method: "audio"  # audio | url
    pause: null             # optional float → client appends [pause Ns] itself
    max_text_length: 5000   # per-request cap (long-form splitter)
    streaming: true
    # webhook_url: ""       # optional async completion webhook
    # base_url: ""          # optional override (default https://api.mosi.cn/v1)
```

`hermes tools` → Text-to-Speech lists **Moss** (badge `paid`, tag
“中文/英文 TTS · 15 内置音色 · 克隆/对话”) and prompts for `MOSS_API_KEY`.

---

## Usage

### Single-voice TTS (standard tool)

```
text_to_speech(text="欢迎使用 Moss API。", provider="moss")
```

- Writes `MEDIA:<path>`; `voice_compatible=True` → Telegram/Matrix/Feishu/
  WhatsApp/Signal bubbles are delivered as Ogg/Opus via the existing
  ffmpeg pipeline.
- Requested `format` map: `mp3→mp3`, `wav→wav`; `ogg/opus/flac` request mp3 and
  transcode with ffmpeg when available (else mp3 + container-repair fallback).
- `speed` and `instructions` are ignored (Moss single-voice has no such param) —
  style/instruction belongs to `moss_voice_design`.
- Pause: set `tts.moss.pause` (float) and Moss appends `[pause Ns]` itself;
  literal `[pause Ns]` markers in the text survive `prepare_spoken_text`.

### Streaming

```
tts.streaming.provider: moss   # or tts.provider: moss (auto)
```

`MossStreamer` is pinned at **48,000 Hz** (live-probed) and yields int16 mono
PCM through the standard `stream_tts_to_speaker` / gateway consumer; a
mid-stream `speech.created.sample_rate` mismatch is logged as a cross-check
only. Byte cap (16 MiB/sentence), interruption latch, and sentence chunking
are the shared `tools/tts_streaming` machinery.

### Dialogue (multi-speaker) — `moss_dialogue_tts`

```
moss_dialogue_tts(
  speakers:  [{"id": "a", "voice_id": "<built-in-or-cloned-id>"},
              {"id": "b", "voice_id": "<id>"}],
  segments:  [{"speaker": "a", "text": "你好，请问今天天气如何？"},
              {"speaker": "b", "text": "今天是晴天。"}],
  output_path: "/path/out.mp3",     # optional
  response_format: "mp3",           # mp3 | wav | ogg | opus | flac
  model: "moss-ttsd",               # optional
  async_mode: false
)
```

Validation: every segment speaker must be declared; empty segments rejected;
per-segment text ≤ `tts.moss.max_text_length`; >20 segments warns. Returns
`{success, file_path, MEDIA, media_tag}` (or `{success, task_id}` with
`async_mode: true`). Dialogue is **not** run through the long-form splitter.

### Voice design — `moss_voice_design`

```
moss_voice_design(instruction="热情洋溢的播客主持人", text="这是声音设计测试。",
                  output_path="/path/design.mp3")
```

Synthesizes audio in the described style. The instruction creates a **style,
not a persisted voice** (Moss returns audio directly). Optional `async_mode`.

### Voice clone & list — `moss_voice_clone` / `moss_voice_list`

```
moss_voice_clone(audio_sample_path="/path/sample.mp3",
                 name="我的音色", description="optional")
# → {success: true, voice_id: "…", voice: {…}}

moss_voice_list()
# → {success: true, count: N, voices: [{id, voice_id, display, language, builtin|cloned, …}]}
```

The returned `voice_id` can be used as `tts.moss.voice_id`, in
`moss_dialogue_tts` speakers, or passed as `voice` to `text_to_speech`.
Cloned voices automatically appear in `list_voices()`.

### Async (single voice)

Provider ABC (`MossProvider.async_synthesize` / `poll_task`):

```
task = MossProvider().async_synthesize("异步任务测试。")
done = MossProvider().poll_task(task["task_id"], timeout=180)
url  = done.get("url") or done.get("result", {}).get("url")
```

Live-verified: `task_id` → `SUCCESS` → downloadable `url` at `done.url`.

---

## Quick start (10 seconds)

```bash
# 1. point Hermes at Moss
hermes auth add moss            # or put MOSS_API_KEY in .env

# 2. pin the provider
#    config.yaml → tts.provider: "moss"

# 3. speak
hermes  # in-session: text_to_speech(provider="moss") or any moss_* tool
```

Run the live E2E with your configured key:

```bash
MOSS_KEY_FILE=/secure/path/moss-key.txt \
  .venv/Scripts/python.exe -m pytest tests/plugins/test_moss_provider_e2e.py -q
# or: MOSS_API_KEY=sk-... pytest ...
```
