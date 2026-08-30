# Jarvis
<img width="1898" height="930" alt="image" src="https://github.com/user-attachments/assets/0dd5973a-8651-4ca7-bbd5-f8d89596db2f" />

Jarvis is a self-hosted personal voice assistant: your PC is the local brain, while an old Surface RT can be a lightweight animated voice/display satellite. It uses an Ollama-compatible local model, SQLite personal memory, a strict tool registry, and an event-driven face rather than a chatbot dashboard.

## Architecture

```text
Surface RT browser → authenticated REST/WebSocket → Jarvis PC server → Ollama / SQLite / BLE lamps
```

The Surface needs only a browser. It does not run Ollama, Python, Node, Bluetooth, or models.

## Requirements

- PC: Python 3.11+, Ollama, Bluetooth if using Lotus lamps.
- Surface: a browser that supports JavaScript, fetch, and WebSocket. Windows RT’s old browser may not expose browser speech recognition; use the text field or PC-side voice input in that case.
- Optional: Lotus Lamp X-compatible lamp and `lotus-lamp` package.

## Quick start (recommended)

From this project folder, run:

```bash
./jarvis
```

This one command creates the virtual environment, installs Jarvis, creates `.env`, generates a private client token, and starts the server using the correct Python interpreter. No `source .venv/bin/activate` or direct `uvicorn` command is required. These setup choices persist; future runs simply start Jarvis. Each launch prints the Surface pairing token clearly.

It does **not** start a second Ollama server. The `address already in use` message in `ollama serve` means Ollama is already running normally—leave it running and start Jarvis instead.

Useful commands on macOS/Linux: `./jarvis lamps` installs BLE support and scans for lamps; `./jarvis test` runs the automated tests. `./jarvis token` displays the persistent token only when you need to pair another device.

On Windows PowerShell, use an explicit virtual environment instead of the bash launcher:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m jarvis
.\.venv\Scripts\python -m pytest
```

To make Jarvis start automatically when you log in to Windows, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_startup.ps1
```

This creates or updates a current-user Scheduled Task named `Jarvis Home Server`. It runs `scripts/start_jarvis_windows.ps1`, which creates `.venv` if needed, installs Jarvis only when imports are missing, then starts `python -m jarvis`. Startup output is appended to `data/jarvis-startup.log`.

## Manual install

```bash
cd /Users/scottgarciajr/Documents/Codex/2026-08-17/i-want-you-to-build-a-2
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

For Bluetooth lamp control:

```bash
./jarvis lamps
```

## Ollama setup

Install Ollama on the PC, pull any tool-capable model you prefer, then set its name in `.env`:

```bash
ollama pull llama3.2
ollama serve
```

Set `OLLAMA_BASE_URL` and `OLLAMA_MODEL` in `.env`. The default `http://127.0.0.1:11434` deliberately keeps Ollama private to the PC. A health check is available at `/api/health`; it returns `ollama: false` if unreachable. Jarvis reports a clear error rather than silently falling back.

## Configure and start the server

Set a long `JARVIS_API_TOKEN` in `.env`. For initial PC testing, leave `JARVIS_BIND_HOST=127.0.0.1`.

```bash
./jarvis
```

For the Surface, set `JARVIS_BIND_HOST` to the PC’s LAN address (or `0.0.0.0` only behind a trusted home router), set `JARVIS_HTTPS=true`, then run `./jarvis` again. Open `https://PC-LAN-IP:8000` on the Surface. Jarvis creates a local certificate in `data/` on first HTTPS launch. If Edge will not offer microphone permission, open `https://PC-LAN-IP:8000/api/cert`, install `jarvis.local.crt` into the Surface user’s Trusted Root Certification Authorities store, then reopen Jarvis. It will ask to pair once; run `./jarvis token` on the PC and paste that value. The token stays in that browser’s session storage only. Browsers opened on the Jarvis PC pair automatically.

## Voice and wake word

The browser client starts listening automatically after microphone access is allowed. On another device, open Jarvis with HTTPS; Chrome and Edge generally will not grant microphone access to an HTTP LAN page or an untrusted HTTPS page. Use the microphone button once if the browser needs a user gesture for its first permission prompt. After permission is granted, Jarvis listens for its name automatically. Say **“Jarvis, can you hear me?”** or say **“Jarvis”**, wait for “Yes?”, then give your request. It ignores speech that does not include the wake phrase and resumes listening after its reply. Safari and the original Windows RT browser do not reliably provide the browser speech-recognition feature; the client displays that limitation clearly instead of pretending to listen.

The default UI is the Jarvis Sphere: a code-generated, animated golden energy sphere inspired by cinematic HUD imagery. It reacts to real Jarvis states from the websocket: idle, listening, thinking, speaking, action execution, success, error, and offline. Color-changing light actions start a partial color spread while the action is pending; the spread finishes only after the real tool/API action succeeds. Live streams such as TV mode and music sync tint the sphere continuously without repeatedly announcing success. Older Surface/Windows browsers automatically use a lighter sphere renderer with fewer particles, a lower frame cap, and no expensive blur effects; Mac, iPhone, and modern desktop browsers keep the full renderer.

The controls panel is hidden by default. Click `Controls` or tap the sphere to show or hide it. Use `Mic` to enable local browser listening or interrupt speech; tapping the sphere is reserved for opening the control GUI. Speech output asks the browser for an English UK voice first and prefers male-sounding system voices such as Daniel, George, Ryan, Arthur, or Thomas when the operating system exposes one. If the browser does not provide a British male voice, Jarvis falls back to the best English UK voice it can find.

Settings includes an Interface selector. `Use default interface` and `Jarvis Sphere` show the sphere; `Classic Face` restores the original face interface. The selection is saved in browser local storage as `jarvisInterface`, and unavailable values safely fall back to the sphere. The display button in the upper-right cycles through face, face-on-black, hidden-black, and full blackout. In full blackout, the screen goes completely black; double-click or press Escape to recover.

For hands-free “Hey Jarvis”, run wake word/STT on the PC, not Surface. Install the optional tools:

```bash
pip install -e '.[voice-server]'
```

When the client Settings panel is set to Microphone: Server, Jarvis uses a PC-side microphone listener. Browsers opened directly on the Jarvis PC start that listener automatically. Remote clients such as the Surface do **not** auto-start the server mic on page load; tap `Mic` when you intentionally want the Jarvis PC microphone to begin listening. This avoids an old client browser accidentally triggering the faster-whisper model load just by opening the page. The listener records short chunks from the Jarvis PC microphone, transcribes them with faster-whisper, listens for the wake phrase, and sends the command into the same assistant service used by typed/browser commands. OpenLily is a relevant reference pipeline; it is not bundled because its local Ollama implementation currently targets Apple Silicon.

Server microphone, chat, tool, Lotus pairing, auto-connect, music color, camera color, and server TV mode debugging is saved automatically to `data/jarvis-debug.jsonl`. Each JSON line includes the event type, timestamp, `jarvis_version`, `git_revision`, `git_dirty`, `log_schema_version`, and the relevant request/outcome data. Server microphone startup writes `server_mic_model` records when faster-whisper begins loading and when it finishes, plus `server_mic_loop` records with tracebacks for normal Python/audio/model errors. Error records include the exception type, message, traceback, and current lamp errors when available, so you can give ChatGPT one file and get useful help. Set `JARVIS_DEBUG_LOG_PATH` to write somewhere else or `JARVIS_DEBUG_LOGGING=false` to turn it off.

## Lotus Lamp setup

Discover devices on the PC:

Run `./jarvis lamps`; for a longer scan, use `./jarvis lamps --timeout 10`.

Copy addresses into `.env`, e.g. `LOTUS_LAMP_DEVICES=25=BE:16:54:00:29:25,49=BE:16:54:00:58:49`. If unset, Jarvis uses those two known LotusLamp X controllers by default. You can also open Settings in the Jarvis GUI, scan from the Jarvis PC, and pair or repair lamps there. GUI pairing updates the active controller list immediately and saves `LOTUS_LAMP_DEVICES` plus friendly names in `LOTUS_LAMP_LABELS`. Available tools are `lotus_turn_on`, `lotus_turn_off`, `lotus_set_color`, `lotus_set_rgb`, `lotus_set_brightness`, `lotus_set_effect`, `lotus_stop_effect`, and `lotus_set_hardware_mode`. Brightness changes preserve the current/last known lamp color rather than forcing white.

Natural commands depend on the selected Ollama model’s tool-calling support, e.g. “turn the TV lights purple”, “set the loft lights to 50 percent”, “what effects do you have?”, “candlelight”, “candlelight then ocean then aurora”, “sync the lights to music”, “match the lights to the camera”, or “TV mode.” Music sync uses the selected microphone mode. Camera matching uses the selected camera mode; server mode uses the Jarvis PC camera and local mode uses the browser camera when supported.

TV Mode is adaptive: it targets all paired lights by default, honors explicit phrases like “TV mode for TV lights,” and includes movie color tuning with Cinema, Vivid, Soft, and Dark Room profiles plus transition speed controls. In server camera mode, TV Mode now runs as a background task on the Jarvis PC. That means the lights keep following the PC camera even if you close the Surface/laptop browser client. The GUI has a `TV Mode` button to start it and a `TV Off` button to stop it. Voice commands such as “turn on TV mode” and “turn off TV mode” use the same server-side path. Starting a normal color, brightness, hardware mode, music sync, or atmosphere effect automatically stops server TV mode first; starting TV mode automatically stops the previous software effect. If the wrong PC camera is chosen, set `JARVIS_CAMERA_INDEX` in `.env`. A successful color tool result broadcasts `visual_state/lights` with the actual RGB hex color, allowing the face ambient glow to match it.

Jarvis also starts a background Lotus auto-connect loop. On launch, it keeps trying to connect to every configured Lotus controller until all paired lamps are reachable. Color writes retry several times too, so a temporary Bluetooth wobble should not require you to manually reconnect every time.

## Future feature handoff guide

If you wish to adjust Jarvis features, use the files that own the behavior you want to change. The fastest bundle for most changes is:

- `README.md`: current operating notes, setup, troubleshooting, and this handoff map.
- `pyproject.toml`: package dependencies, extras, pytest config, and the console entry point.
- `.env` with secrets removed: runtime settings such as `OLLAMA_MODEL`, `JARVIS_CAMERA_INDEX`, and Lotus lamp addresses.
- `src/jarvis/main.py`: FastAPI app, REST/WebSocket endpoints, server camera capture, server TV mode, Lotus pairing routes, and app startup/shutdown tasks.
- `src/jarvis/service.py`: assistant prompt, direct command parsing, Ollama chat flow, tool-call handling, and assistant debug traces.
- `src/jarvis/tools/lotus_lamp.py`: Lotus BLE protocol, target matching, retries, effects, and all light-control tool definitions.
- `src/jarvis/client/index.html`: browser GUI structure and control IDs.
- `src/jarvis/client/app.js`: browser behavior, GUI event handlers, websocket events, local microphone/camera mode, and client-side state.
- `src/jarvis/client/sphere.js` and `src/jarvis/client/sphere.css`: animated Jarvis Sphere renderer, visual state mapping helpers, color parsing, color propagation animation, Surface/older-browser performance mode, and sphere layout.
- `src/jarvis/client/style.css`, `src/jarvis/client/modes.css`, `src/jarvis/client/expressions.css`, and `src/jarvis/client/pairing.css`: visual styling for the classic face, controls, display modes, expressions, and pairing/settings panels.
- `scripts/start_jarvis_windows.ps1` and `scripts/install_windows_startup.ps1`: Windows boot/login startup runner and Scheduled Task installer.
- `src/jarvis/debug_log.py`: JSONL debug record schema and version/source metadata.
- `src/jarvis/config.py`: environment variable names and parsed settings.
- `src/jarvis/events.py`: event objects and pub/sub bus used by tools, server tasks, and browser clients.
- `src/jarvis/server_voice.py`: PC-side microphone, wake phrase, transcription, and voice debug logs.
- `tests/`: regression tests. Include the test file nearest the feature plus any failing test output.
- `data/jarvis-debug.jsonl`: recent runtime logs when something failed. Remove anything private before sharing.

For Lotus/light issues, include `src/jarvis/main.py`, `src/jarvis/tools/lotus_lamp.py`, `src/jarvis/client/app.js`, `src/jarvis/client/index.html`, `.env` without the API token, and the relevant `data/jarvis-debug.jsonl` lines. For voice/model issues, include `src/jarvis/service.py`, `src/jarvis/server_voice.py`, `src/jarvis/ollama.py`, `.env` without secrets, and debug logs. For GUI-only changes, include the client HTML/CSS/JS files plus screenshots or a short description of the target workflow.

For startup issues, include `scripts/start_jarvis_windows.ps1`, `scripts/install_windows_startup.ps1`, `.env` without secrets, and `data/jarvis-startup.log`.

## Memory, tools, and visual states

Say “remember that…” to save an explicit memory; use “what do you remember about…” to retrieve it. Conversation records, explicit memories, and future document knowledge live in separate SQLite tables.

Tools have schemas, validation, permissions, an allowlisted registry, and structured results. They can return presentation-neutral events. Client states currently include idle, listening, thinking, speaking, error, and visual states such as lights. Weather/calendar/music are intentionally pluggable additions, not hard-coded UI paths.

## Future Canvas integration

Canvas authentication is not implemented. The provider interface and safe stub live in `src/jarvis/integrations/calendar.py`. Add a Canvas API provider there, then a calendar tool; no core assistant or client restructure is needed.

## Security

Use a long API token, bind only to a trusted LAN, and keep Ollama bound locally. Jarvis does not offer arbitrary shell or Python execution. Tool inputs are schema-validated and unknown tools are rejected. Avoid putting secrets in source control; `.env` is ignored.

## Test

```bash
./jarvis test
```

Tests use fake Ollama and mock lamps; no lamp, Surface, Bluetooth radio, or Ollama server is required.

## Troubleshooting

- `ModuleNotFoundError: No module named 'jarvis'` after running `uvicorn`: use `./jarvis` instead. Your shell selected a global Uvicorn linked to a different Python; the launcher always uses Jarvis’s own virtual environment.
- On Windows, `./jarvis` may fail because it is a bash script. Use `.\.venv\Scripts\python -m jarvis` and `.\.venv\Scripts\python -m pytest`.
- If Jarvis does not start after Windows login, open Task Scheduler and check the `Jarvis Home Server` task history, then inspect `data/jarvis-startup.log`.
- To reinstall/update the Windows startup task, rerun `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_startup.ps1`.
- To remove startup behavior, delete the `Jarvis Home Server` task in Task Scheduler.
- `ollama serve` says `address already in use`: that is good—Ollama is already running. Do not start another copy.
- `Ollama is unavailable`: start Ollama and confirm `OLLAMA_BASE_URL`.
- Lamp setup import error: install `.[lotus]`, then make sure the PC Bluetooth adapter can see the lamp.
- Lotus lamps will not connect: leave Jarvis running for a minute; the startup auto-connect loop keeps trying until all configured lamps are reachable. Check `data/jarvis-debug.jsonl` for `lotus_auto_connect` and `server_tv_mode` errors.
- To disable startup Lotus retries for a special dev/test run, set `JARVIS_LOTUS_AUTO_CONNECT=false`.
- TV mode stops when the client closes: use Camera: Server in Settings. Local browser camera mode necessarily stops with the browser, but server camera TV mode keeps running on the Jarvis PC until you press `TV Off` or say “turn off TV mode.”
- Surface cannot hear you: use the text field or move wake-word/STT to the PC; Windows RT’s browser commonly lacks speech recognition.
- Browser voice recognition feels stuck on `Listening...`: check `data/jarvis-debug.jsonl` for `client_event` records such as `speech_final`, `speech_followup_result`, and `speech_submit_command`. These show whether the browser heard a final wake phrase, captured follow-up text, and submitted it to `/api/chat`.
- Repeated `/api/client/log` requests: the browser throttles repeated speech/debug events, but useful entries still go to `data/jarvis-debug.jsonl`. If the console shows suppressed `asyncio_connection_reset` records in the debug file, that usually means an old client browser closed or reset a connection abruptly.
- WebSocket closes immediately: re-enter the same `JARVIS_API_TOKEN` configured on the server.

See [DESIGN.md](DESIGN.md) for the reference audit and detailed boundaries.
# jarvis
