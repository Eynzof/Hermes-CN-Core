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

---

## Transcription — `moss_transcribe` (speech-to-text + diarization)

The Moss plugin also provides **speech-to-text** through two surfaces:

1. **Gateway voice messages** — set `stt.provider: moss` in config.yaml and
   the standard voice-message pipeline routes through the plugin's
   `MossTranscriptionProvider` (registered via
   `register_transcription_provider`; `moss` is deliberately absent from
   `BUILTIN_STT_PROVIDERS`, so plugin dispatch fires — no core change).
2. **Agent-facing tool** — `moss_transcribe` (gated on a configured key).

### Config (`stt.moss`)

```yaml
stt:
  provider: "moss"        # route gateway voice messages here
  moss:
    model: "moss-transcribe-1.0"   # or moss-transcribe-diarize-pro
    diarize: false                 # true → forces diarize-pro + speaker segments
    response_format: "json"        # json | text | diarized_json
    max_file_size: 536870912       # 512 MB (Moss allows up to 512 MB)
    prompt: ""                     # optional keyterms (vocabulary boost; diarize-pro only)
```

Credentials are **shared with TTS** (`MOSS_API_KEY` env/.env,
`tts.moss.api_key`, `hermes auth add moss`, `MOSS_KEY_FILE`) — no new
secrets. `hermes tools` → Speech-to-Text lists **Moss** (badge `paid`).

### Usage

```text
moss_transcribe(
  audio_path: "/path/clip.wav",        # local file, `file_id:<id>`, or public URL
  diarize: false,                      # true → moss-transcribe-diarize-pro + segments
  model: "moss-transcribe-1.0",        # optional (auto-forced when diarize=true)
  keyterms: ["术语", "boost"],         # ≤20, ≤30 chars each; diarize-pro only
  response_format: "json",             # json | text | diarized_json
  async_mode: false,                   # true → returns task_id; poll with task_id=...
)
# → {success, transcript, provider, model, duration?, segments?}
```

- **Multi-speaker diarization**: `diarize=true` forces
  `moss-transcribe-diarize-pro`; the result adds `segments` normalized to
  `{start, end, text, speaker}` (speaker labels like `S01`).
- **Input kinds**: local file → multipart upload; `file_id:<id>` / URL →
  JSON pass-through. Localhost/loopback/private URLs are rejected
  client-side (shared security rule).
- **Async**: `async_mode=true` returns `{task_id}`; call `moss_transcribe`
  again with that `task_id` to poll (reuses the same
  `GET /v1/audio/tasks/{task_id}` endpoint as TTS).
- **Caps**: ≤512 MB audio; `keyterms` ≤20 entries × ≤30 chars (dropped on
  the plain model). `language` is a best-effort hint (Moss has no language
  param — logged and ignored).

Live-verified round-trip: synthesize a WAV → `moss_transcribe` returns the
spoken text; a two-speaker dialogue diarizes into labeled segments.

---

## Image & video understanding — `moss_vision` (MOSS-VL)

`moss_vision` calls `POST /v1/responses` with the `moss-vl-1.0` model for
image/video understanding, OCR, captioning, and video Q&A. It is a gated
tool in the `moss` toolset (check_fn on a configured key) and is
deliberately **not** wired into the core `vision_analyze` router — a
third-party SaaS backend lives at the edge.

```text
moss_vision(
  instruction: "OCR this receipt and return the total.",
  images: ["/path/a.png", "file_id:img-1"],   # ≤5 images total
  # image_urls: ["https://.../a.png"],        # explicit public URLs
  # video: "/path/clip.mp4",                  # OR exactly 1 video (never mixed)
  # video_url: "https://.../v.mp4",
  max_output_tokens: 2048,                    # 1–8192 (truncation → warning)
)
# → {success, text, status, provider: "moss", model, warning?}
```

Constraints honored from the docs:

- **1–5 images OR exactly 1 video — never mixed** (returns an error
  otherwise).
- Per item either a URL or a local file / `file_id` — local media is
  uploaded via `POST /v1/files` (purpose `image` / `video`) and passed as
  `file_id`; URLs must be public.
- Caps: image ≤30 MB each; video ≤200 MB; `max_output_tokens` 1–8192.
- When `status == "incomplete"` with
  `incomplete_details.reason == "max_output_tokens"`, the result includes a
  `warning` — retry with a higher `max_output_tokens` for the full answer.

Live-verified: an OCR image (`HELLO MOSS 2026`) transcribed to text with
`status: "completed"`, both via local-file upload and via a pre-uploaded
`file_id`.
