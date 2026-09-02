from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def run(command: list[str], *, cwd: Path | None = None, stdin: str | None = None) -> None:
    subprocess.run(command, cwd=cwd, input=stdin, text=stdin is not None, check=True)


def probe_duration(ffprobe: Path, media: Path) -> float:
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(media)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def discover(root: Path, pattern: str) -> Path:
    matches = list(root.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"Could not find {pattern} below {root}")
    return matches[0]


def sd_request(url: str, payload: dict, attempts: int = 2) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=360) as response:
                body = json.loads(response.read().decode("utf-8"))
            return base64.b64decode(body["images"][0].split(",", 1)[-1])
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            if attempt == attempts:
                raise RuntimeError(f"Stable Diffusion request failed: {exc}") from exc
            time.sleep(3)
    raise AssertionError("unreachable")


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build(args: argparse.Namespace) -> Path:
    project_root = Path(__file__).resolve().parents[1]
    episode_dir = args.episode.resolve()
    output_dir = episode_dir / "output"
    work = episode_dir / "temp"
    input_dir = episode_dir / "input"
    output_dir.mkdir(parents=True, exist_ok=True)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    scenes = manifest["scenes"]
    ffmpeg = discover(project_root, "_shared/tools/ffmpeg/**/bin/ffmpeg.exe")
    ffprobe = discover(project_root, "_shared/tools/ffmpeg/**/bin/ffprobe.exe")
    piper = project_root / ".venv/Scripts/piper.exe"
    voice = discover(project_root, "_shared/voices/**/*.onnx")

    style = (
        "masterpiece, best quality, cinematic anime key frame, original dark fantasy anime, "
        "clean expressive faces, detailed eyes, dramatic lighting, deep navy and silver color palette, "
        "Asterion magical academy, consistent character design, 16:9 composition"
    )
    negative = (
        "worst quality, low quality, lowres, blurry, bad anatomy, bad hands, extra fingers, "
        "missing fingers, duplicate person, text, letters, watermark, logo, speech bubble, "
        "photorealistic, 3d render, chibi, cropped face"
    )

    image_paths: list[Path] = []
    audio_paths: list[Path] = []
    audio_durations: list[float] = []
    for index, scene in enumerate(scenes, start=1):
        print(f"[{index}/{len(scenes)}] Generating image", flush=True)
        image_path = work / f"image-{index:02d}.png"
        payload = {
            "prompt": f"{style}, {scene['prompt']}",
            "negative_prompt": negative,
            "steps": args.steps,
            "width": 512,
            "height": 288,
            "sampler_name": "Euler a",
            "cfg_scale": 7,
            "seed": int(scene.get("seed", 240901)),
            "batch_size": 1,
            "n_iter": 1,
        }
        image_path.write_bytes(sd_request(f"{args.sd_url.rstrip('/')}/sdapi/v1/txt2img", payload))
        image_paths.append(image_path)

        print(f"[{index}/{len(scenes)}] Synthesizing narration", flush=True)
        audio_path = work / f"voice-{index:02d}.wav"
        run(
            [
                str(piper), "--model", str(voice), "--output_file", str(audio_path),
                "--length-scale", str(args.voice_length_scale), "--sentence-silence", "0.12",
            ],
            stdin=scene["narration"].strip(),
        )
        audio_paths.append(audio_path)
        audio_durations.append(probe_duration(ffprobe, audio_path))

    target = float(args.duration)
    minimum_gap = 0.35
    narration_budget = target - len(scenes) * minimum_gap
    raw_total = sum(audio_durations)
    if raw_total > narration_budget:
        speed = raw_total / narration_budget
        if speed > 2:
            raise RuntimeError("Narration is too long to fit the requested duration.")
        adjusted_paths: list[Path] = []
        adjusted_durations: list[float] = []
        for index, source in enumerate(audio_paths, start=1):
            adjusted = work / f"voice-adjusted-{index:02d}.wav"
            run([str(ffmpeg), "-y", "-i", str(source), "-af", f"atempo={speed:.6f}", str(adjusted)])
            adjusted_paths.append(adjusted)
            adjusted_durations.append(probe_duration(ffprobe, adjusted))
        audio_paths, audio_durations = adjusted_paths, adjusted_durations
        raw_total = sum(audio_durations)

    gap = (target - raw_total) / len(scenes)
    scene_durations = [duration + gap for duration in audio_durations]

    padded_audio: list[Path] = []
    video_clips: list[Path] = []
    subtitle_blocks: list[str] = []
    cursor = 0.0
    for index, (scene, image, audio, audio_duration, scene_duration) in enumerate(
        zip(scenes, image_paths, audio_paths, audio_durations, scene_durations), start=1
    ):
        padded = work / f"audio-{index:02d}.wav"
        run([
            str(ffmpeg), "-y", "-i", str(audio), "-af", "apad", "-t", f"{scene_duration:.3f}",
            "-ar", "48000", "-ac", "2", str(padded),
        ])
        padded_audio.append(padded)

        frames = round(scene_duration * 24)
        direction = scene.get("motion", "zoom_in")
        zoom = "min(zoom+0.00035,1.075)" if direction != "zoom_out" else "max(1.075-on*0.00035,1.0)"
        x = "iw/2-(iw/zoom/2)"
        if direction == "pan_left":
            x = "max(0,iw-iw/zoom-on*0.35)"
        elif direction == "pan_right":
            x = "min(iw-iw/zoom,on*0.35)"
        video = work / f"video-{index:02d}.mp4"
        filter_chain = (
            "scale=1344:756:flags=lanczos,crop=1280:720," 
            f"zoompan=z='{zoom}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=24,format=yuv420p"
        )
        run([
            str(ffmpeg), "-y", "-loop", "1", "-i", str(image), "-vf", filter_chain,
            "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", str(video),
        ])
        video_clips.append(video)

        subtitle_end = cursor + min(audio_duration + 0.12, scene_duration - 0.05)
        subtitle_blocks.append(
            f"{index}\n{srt_time(cursor)} --> {srt_time(subtitle_end)}\n"
            f"{scene['narration'].strip()}\n{scene['zh'].strip()}\n"
        )
        cursor += scene_duration

    audio_list = work / "audio-list.txt"
    audio_list.write_text("".join(f"file '{path.as_posix()}'\n" for path in padded_audio), encoding="utf-8")
    narration = work / "narration.wav"
    run([str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(audio_list), "-c", "copy", str(narration)])

    mixed_audio = work / "mixed.wav"
    ambient = f"anoisesrc=color=pink:amplitude=0.018:duration={target}:sample_rate=48000"
    run([
        str(ffmpeg), "-y", "-i", str(narration), "-f", "lavfi", "-i", ambient,
        "-filter_complex", "[0:a]volume=1.25[n];[1:a]lowpass=f=420,volume=0.32,afade=t=in:st=0:d=2,afade=t=out:st=174:d=4[a];[n][a]amix=inputs=2:duration=first:normalize=0[out]",
        "-map", "[out]", "-ar", "48000", "-ac", "2", str(mixed_audio),
    ])

    video_list = work / "video-list.txt"
    video_list.write_text("".join(f"file '{path.as_posix()}'\n" for path in video_clips), encoding="utf-8")
    assembled = work / "assembled.mp4"
    run([str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(video_list), "-c", "copy", str(assembled)])

    subtitles = work / "bilingual.srt"
    subtitles.write_text("\n".join(subtitle_blocks), encoding="utf-8-sig")
    output = output_dir / manifest.get("output_name", "midnight-parcel-ep-001.mp4")
    subtitle_filter = (
        "subtitles=bilingual.srt:force_style='FontName=Microsoft YaHei,FontSize=17,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00101018,BorderStyle=1,Outline=2,Shadow=1,MarginV=28,Alignment=2'"
    )
    run([
        str(ffmpeg), "-y", "-i", str(assembled), "-i", str(mixed_audio),
        "-vf", subtitle_filter, "-map", "0:v:0", "-map", "1:a:0", "-t", str(target),
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
    ], cwd=work)

    final_duration = probe_duration(ffprobe, output)
    if not output.is_file() or output.stat().st_size < 1_000_000 or final_duration > 180.05:
        raise RuntimeError(f"Final validation failed: duration={final_duration:.3f}s")

    shutil.rmtree(work)
    if input_dir.exists():
        shutil.rmtree(input_dir)
    print(f"FINAL={output}")
    print(f"DURATION={final_duration:.3f}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and render a narrated AI-anime episode.")
    parser.add_argument("--episode", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sd-url", default="http://127.0.0.1:7860")
    parser.add_argument("--duration", type=float, default=178.0)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--voice-length-scale", type=float, default=1.06)
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
