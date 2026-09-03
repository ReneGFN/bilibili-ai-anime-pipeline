# Bilibili AI Anime Pipeline

Local-first, reusable production pipeline for original narrated anime shorts. It builds a video from scene images, a voice-over, optional mouth-flap timing, subtitles, and FFmpeg motion effects. Episode scripts, generated media, credentials, and AI models are intentionally excluded from Git.

## What it does

1. Creates a WAV narration with Piper (optional if a WAV already exists).
2. Produces Rhubarb mouth-cue JSON (optional).
3. Renders each still image with a subtle Ken Burns movement.
4. Concatenates scenes, adds narration, and muxes English and Simplified Chinese subtitle tracks.
5. Verifies the final MP4 before deleting `temp/`.

The script is deliberately local-first. It does **not** generate images or send account cookies to a third party. Image generation is kept separate because AMD/DirectML installations vary greatly. Uploading is also a manual last step until compatibility with the international Bilibili service is validated.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/), available in `PATH`
- [Piper](https://github.com/k-rks/piper) and an English voice model (only when generating narration)
- [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) (optional)

Install the package from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## Episode layout (kept out of Git)

```text
<series>/episodes/ep-001/
├── input/
│   ├── narration.txt
│   ├── scenes.yml
│   ├── narration.wav          # optional when Piper is configured
│   ├── en.srt                 # optional
│   ├── zh-Hans.srt            # optional
│   └── images/
│       ├── 01.png
│       └── 02.png
├── temp/                      # disposable; auto-deleted only after final validation
└── output/
    └── ep-001.mp4
```

Copy [`examples/scenes.yml`](examples/scenes.yml) into `input/scenes.yml`, then change image names, durations, and metadata.

## Build

```powershell
bili-anime build --episode .\midnight-parcel\episodes\ep-001 --config .\input\scenes.yml
```

Run without changing files first:

```powershell
bili-anime build --episode .\midnight-parcel\episodes\ep-001 --config .\input\scenes.yml --dry-run
```

Use an existing narration WAV and keep temporary files for inspection:

```powershell
bili-anime build --episode .\midnight-parcel\episodes\ep-001 --config .\input\scenes.yml --keep-temp
```

`cleanup_temp` defaults to `true`. Cleanup happens only after FFprobe confirms that the final MP4 exists, has an audio stream, and is no longer than 180 seconds.

## Channels Used
https://www.tiktok.com/@moonframetales
https://www.youtube.com/@moonframetales2

## Important notes

- Keep each short within 180 seconds. The pipeline rejects longer results.
- Use only original characters, art, music, and narrations for content marked original.
- The tool muxes subtitles as selectable MP4 tracks; it does not burn them into the image.
- Do not commit `input/`, `temp/`, `output/`, `.env`, voice models, or cookies.

