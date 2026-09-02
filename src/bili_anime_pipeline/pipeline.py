from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


class PipelineError(RuntimeError):
    pass


def _run(command: list[str], dry_run: bool) -> None:
    print("$", subprocess.list2cmdline(command))
    if not dry_run:
        try:
            subprocess.run(command, check=True)
        except FileNotFoundError as exc:
            raise PipelineError(f"Tool not found: {command[0]}") from exc
        except subprocess.CalledProcessError as exc:
            raise PipelineError(f"Command failed with exit code {exc.returncode}: {command[0]}") from exc


def _load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PipelineError(f"Config not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not data.get("scenes"):
        raise PipelineError("Config needs a non-empty 'scenes' list.")
    return data


def _resolve_input(input_dir: Path, value: str | None) -> Path | None:
    return input_dir / value if value else None


def _motion_filter(motion: str, fps: int, width: int, height: int) -> str:
    # zoompan renders a 1080p moving crop from a high-resolution still.
    zoom = "min(zoom+0.00045,1.08)" if motion == "zoom_in" else "max(zoom-0.00045,1.0)"
    x = "iw/2-(iw/zoom/2)"
    if motion == "pan_right":
        x = "min(iw-iw/zoom,x+1.2)"
    elif motion == "pan_left":
        x = "max(0,x-1.2)"
    return f"zoompan=z='{zoom}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}"


def _render_scene(ffmpeg: str, scene: dict[str, Any], input_dir: Path, destination: Path, fps: int, width: int, height: int, dry_run: bool) -> None:
    image = _resolve_input(input_dir, scene.get("image"))
    duration = float(scene.get("duration", 0))
    if not image or not image.is_file():
        raise PipelineError(f"Scene image not found: {image}")
    if duration <= 0:
        raise PipelineError(f"Scene duration must be positive: {image.name}")
    frames = max(1, round(duration * fps))
    filter_chain = _motion_filter(str(scene.get("motion", "zoom_in")), fps, width, height)
    _run([ffmpeg, "-y", "-loop", "1", "-i", str(image), "-vf", filter_chain, "-frames:v", str(frames), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(destination)], dry_run)


def _make_narration(tools: dict[str, Any], audio: dict[str, Any], input_dir: Path, temp_dir: Path, dry_run: bool) -> Path:
    destination = temp_dir / "narration.wav"
    supplied = _resolve_input(input_dir, audio.get("narration_wav"))
    if supplied and supplied.is_file():
        if not dry_run:
            shutil.copy2(supplied, destination)
        return destination
    piper, voice = tools.get("piper"), tools.get("piper_voice")
    text_file = _resolve_input(input_dir, audio.get("narration_text"))
    if not piper or not voice or not text_file or not text_file.is_file():
        raise PipelineError("Supply input/narration.wav or configure Piper, Piper voice, and narration text.")
    text = text_file.read_text(encoding="utf-8").strip()
    if not text:
        raise PipelineError("Narration text is empty.")
    command = [str(piper), "--model", str(voice), "--output_file", str(destination)]
    print("$", subprocess.list2cmdline(command), "< narration.txt")
    if not dry_run:
        subprocess.run(command, input=text, text=True, check=True)
    return destination


def _rhubarb_cues(rhubarb: str | None, narration: Path, temp_dir: Path, dry_run: bool) -> None:
    if rhubarb:
        _run([str(rhubarb), "-f", "json", "-o", str(temp_dir / "mouth-cues.json"), str(narration)], dry_run)


def _verify(ffprobe: str, final_video: Path, max_duration: float, dry_run: bool) -> None:
    if dry_run:
        return
    query = [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(final_video)]
    try:
        result = subprocess.run(query, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise PipelineError("Final video could not be verified with FFprobe.") from exc
    report = json.loads(result.stdout)
    duration = float(report.get("format", {}).get("duration", 0))
    has_audio = any(stream.get("codec_type") == "audio" for stream in report.get("streams", []))
    if not final_video.is_file() or duration <= 0 or duration > max_duration or not has_audio:
        raise PipelineError("Final MP4 failed validation; temp files were kept.")


def build_episode(episode_dir: Path, config_arg: Path, keep_temp: bool, dry_run: bool) -> None:
    episode_dir = episode_dir.resolve()
    input_dir, temp_dir, output_dir = (episode_dir / name for name in ("input", "temp", "output"))
    config_path = config_arg if config_arg.is_absolute() else episode_dir / config_arg
    config = _load_config(config_path)
    project, tools, audio = (config.get(key, {}) for key in ("project", "tools", "audio"))
    width, height, fps = int(project.get("width", 1920)), int(project.get("height", 1080)), int(project.get("fps", 24))
    max_duration = float(project.get("max_duration_seconds", 180))
    total = sum(float(scene.get("duration", 0)) for scene in config["scenes"])
    if total > max_duration:
        raise PipelineError(f"Scenes total {total:.1f}s, above the {max_duration:.0f}s limit.")
    if not dry_run:
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
    narration = _make_narration(tools, audio, input_dir, temp_dir, dry_run)
    _rhubarb_cues(tools.get("rhubarb"), narration, temp_dir, dry_run)
    clips: list[Path] = []
    for index, scene in enumerate(config["scenes"], start=1):
        clip = temp_dir / f"scene-{index:02d}.mp4"
        _render_scene(str(tools.get("ffmpeg", "ffmpeg")), scene, input_dir, clip, fps, width, height, dry_run)
        clips.append(clip)
    concat_list = temp_dir / "concat.txt"
    if not dry_run:
        concat_list.write_text("".join(f"file '{clip.as_posix()}'\\n" for clip in clips), encoding="utf-8")
    assembled = temp_dir / "assembled.mp4"
    ffmpeg = str(tools.get("ffmpeg", "ffmpeg"))
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-i", str(narration), "-c:v", "copy", "-c:a", "aac", "-shortest", str(assembled)], dry_run)
    output_name = str(project.get("output_name", "episode.mp4"))
    final_video = output_dir / output_name
    command = [ffmpeg, "-y", "-i", str(assembled)]
    subtitle_inputs: list[tuple[dict[str, Any], Path]] = []
    for subtitle in config.get("subtitles", []):
        subtitle_path = _resolve_input(input_dir, subtitle.get("file"))
        if subtitle_path and subtitle_path.is_file():
            command.extend(["-i", str(subtitle_path)])
            subtitle_inputs.append((subtitle, subtitle_path))
    command.extend(["-map", "0:v", "-map", "0:a", "-c:v", "copy", "-c:a", "copy"])
    for index, (subtitle, _) in enumerate(subtitle_inputs, start=1):
        command.extend(["-map", f"{index}:0", f"-metadata:s:s:{index - 1}", f"language={subtitle.get('language', 'eng')}", f"-metadata:s:s:{index - 1}", f"title={subtitle.get('title', 'Subtitles')}"])
    command.extend(["-c:s", "mov_text", str(final_video)])
    _run(command, dry_run)
    _verify(str(tools.get("ffprobe", "ffprobe")), final_video, max_duration, dry_run)
    if not dry_run and bool(project.get("cleanup_temp", True)) and not keep_temp:
        shutil.rmtree(temp_dir)
        print(f"Validated final video; removed {temp_dir}")
    print(f"Done: {final_video}")

