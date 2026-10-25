"""Resumable, local long-form narration."""

from __future__ import annotations

import hashlib
import gc
import json
import os
import re
import logging
import subprocess
import sys
import unicodedata
import wave
from collections import Counter
from pathlib import Path
from threading import Event
from typing import Callable

CHUNK_CHARS = 200
GAP_MS = 130
LOGGER = logging.getLogger(__name__)


def split_script(text: str) -> list[str]:
    """Preserve the complete script while keeping each model call bounded."""
    if not text.strip():
        raise ValueError("O roteiro está vazio.")
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    clauses: list[str] = []
    for sentence in sentences:
        if len(sentence) > CHUNK_CHARS and ", " in sentence:
            fragments = sentence.split(", ")
            clauses.extend(fragment + ("," if index < len(fragments) - 1 else "")
                           for index, fragment in enumerate(fragments))
        else:
            clauses.append(sentence)
    chunks: list[str] = []
    for sentence in clauses:
        words = sentence.split()
        pieces: list[str] = []
        current: list[str] = []
        for word in words:
            if current and len(" ".join((*current, word))) > CHUNK_CHARS:
                pieces.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            pieces.append(" ".join(current))
        if len(pieces) > 1 and len(pieces[-1]) < 35:
            previous = pieces[-2].split()
            last = pieces[-1].split()
            while len(" ".join(last)) < 35 and len(previous) > 1:
                last.insert(0, previous.pop())
            pieces[-2:] = [" ".join(previous), " ".join(last)]
        for piece in pieces:
            if chunks and len(chunks[-1]) + 1 + len(piece) <= CHUNK_CHARS:
                chunks[-1] += " " + piece
            else:
                chunks.append(piece)
    if not chunks or any(not chunk.strip() or len(chunk) > CHUNK_CHARS for chunk in chunks):
        raise ValueError("Não foi possível dividir o roteiro sem perder texto.")
    return chunks


def prepare_reference(source: Path, destination: Path, ffmpeg: str, seconds: int = 35) -> Path:
    """Decode a reference; fall back to the start for short recordings."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".partial.wav")
    try:
        for start in (20, 0):
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                 "-t", str(seconds), "-i", str(source), "-ac", "1", "-ar", "24000", str(partial)],
                check=True, capture_output=True, text=True,
            )
            with wave.open(str(partial), "rb") as reader:
                if reader.getnframes() >= 24000 * 5:
                    break
        with wave.open(str(partial), "rb") as reader:
            if reader.getnframes() < 24000 * 5:
                raise ValueError("A referência precisa ter ao menos 5 segundos de fala.")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _valid_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as reader:
            return reader.getnframes() > 0 and reader.getnchannels() == 1
    except (OSError, EOFError, wave.Error):
        return False


def combine_wavs(parts: list[Path], target: Path, gap_ms: int = GAP_MS,
                 max_data_bytes: int = 3_500_000_000) -> list[Path]:
    """Stream to WAV volumes below the 4 GB RIFF limit, without holding them in RAM."""
    if not parts or any(not _valid_wav(part) for part in parts):
        raise ValueError("Há trechos de áudio ausentes ou vazios; exportação cancelada.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
    silence = b"\x00" * (params.framerate * gap_ms // 1000 * params.sampwidth)
    volumes: list[Path] = []
    output = None
    partial = None
    volume_bytes = 0
    try:
        for index, part in enumerate(parts):
            with wave.open(str(part), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (
                    params.nchannels, params.sampwidth, params.framerate
                ):
                    raise ValueError(f"Formato de áudio diferente no trecho {index + 1}.")
                source_bytes = source.getnframes() * params.sampwidth
                if source_bytes > max_data_bytes:
                    raise ValueError(f"Trecho {index + 1} excede o limite de um WAV.")
                gap = len(silence) if volume_bytes else 0
                if output is None or volume_bytes + gap + source_bytes > max_data_bytes:
                    if output is not None:
                        output.close()
                        os.replace(partial, volumes[-1])
                    name = target if not volumes else target.with_name(f"{target.stem}-parte-{len(volumes)+1:03d}.wav")
                    volumes.append(name)
                    partial = name.with_suffix(".partial.wav")
                    output = wave.open(str(partial), "wb")
                    output.setnchannels(params.nchannels)
                    output.setsampwidth(params.sampwidth)
                    output.setframerate(params.framerate)
                    volume_bytes = 0
                    gap = 0
                if gap:
                    output.writeframes(silence)
                    volume_bytes += gap
                while frames := source.readframes(65536):
                    output.writeframes(frames)
                    volume_bytes += len(frames)
        output.close()
        output = None
        os.replace(partial, volumes[-1])
        if len(volumes) > 1:
            target.with_suffix(".volumes.json").write_text(
                json.dumps([str(path) for path in volumes], ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        if output is not None:
            output.close()
        if partial is not None:
            partial.unlink(missing_ok=True)
    return volumes


class ChatterboxEngine:
    """Lazy model load; ROCm's PyTorch exposes AMD devices as CUDA devices."""

    def __init__(self, device: str = "auto") -> None:
        import torch
        import soundfile
        import chatterbox.mtl_tts as mtl_tts
        import chatterbox.models.t3.t3 as t3_module
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        from chatterbox.models.s3tokenizer import SOS, EOS

        def trim_speech_tokens(tokens):
            """Handle repeated special markers emitted by stochastic generation."""
            flat = tokens.reshape(-1)
            starts = (flat == SOS).nonzero(as_tuple=True)[0]
            start = int(starts[0]) + 1 if starts.numel() else 0
            ends = (flat[start:] == EOS).nonzero(as_tuple=True)[0]
            end = start + int(ends[0]) if ends.numel() else flat.numel()
            return flat[start:end]

        mtl_tts.drop_invalid_tokens = trim_speech_tokens
        # The API always pads both markers immediately before inference. ROCm's
        # reduction sometimes reports zero for the upstream assertion on GPU.
        t3_module._ensure_BOT_EOT = lambda text_tokens, hp: None

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.soundfile = soundfile
        self.model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
        self._reference_path: Path | None = None

    def render(self, text: str, reference: Path, output: Path) -> None:
        if self._reference_path != reference:
            self.model.prepare_conditionals(str(reference))
            self._reference_path = reference
        audio = self.model.generate(text, language_id="pt")
        partial = output.with_suffix(".partial.wav")
        try:
            samples = audio.detach().cpu().numpy().reshape(-1)
            self.soundfile.write(str(partial), samples, self.model.sr, subtype="PCM_16")
            if not _valid_wav(partial):
                raise RuntimeError("O motor retornou um trecho vazio.")
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)


class BrazilianChatterboxEngine(ChatterboxEngine):
    """Use the exact decoder shipped in ResembleAI's pt-BR demo Space."""

    def __init__(self, device: str = "auto") -> None:
        import importlib.util
        import torch
        import soundfile
        from huggingface_hub import hf_hub_download, snapshot_download

        here = Path(__file__).resolve().parent
        cache = here / "data/hf/hub"
        source = here / "data/ptbr-space"
        snapshot_download(
            "ResembleAI/Chatterbox-Multilingual-TTS-pt-br", repo_type="space",
            revision="9e515821e826e207cd617a0fdd0223899ed108ea",
            local_dir=source,
            allow_patterns=["chatterbox/src/chatterbox/*.py", "chatterbox/src/chatterbox/**/*.py"],
        )
        package_dir = source / "chatterbox/src/chatterbox"
        package_name = "ptbr_chatterbox"
        if package_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                package_name, package_dir / "__init__.py",
                submodule_search_locations=[str(package_dir)],
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Não foi possível carregar o motor pt-BR oficial.")
            package = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = package
            spec.loader.exec_module(package)
        package = sys.modules[package_name]
        tts_module = sys.modules[f"{package_name}.tts"]
        from ptbr_chatterbox.models.s3tokenizer import SOS, EOS
        import ptbr_chatterbox.models.t3.t3 as t3_module

        def trim_speech_tokens(tokens):
            flat = tokens.reshape(-1)
            starts = (flat == SOS).nonzero(as_tuple=True)[0]
            start = int(starts[0]) + 1 if starts.numel() else 0
            ends = (flat[start:] == EOS).nonzero(as_tuple=True)[0]
            end = start + int(ends[0]) if ends.numel() else flat.numel()
            return flat[start:end]

        tts_module.drop_invalid_tokens = trim_speech_tokens
        t3_module._ensure_BOT_EOT = lambda text_tokens, hp: None
        model_dir = here / "data/ptbr-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        required = [
            ("ResembleAI/chatterbox", "ve.pt"),
            ("ResembleAI/chatterbox", "conds.pt"),
            ("ResembleAI/chatterbox", "grapheme_mtl_merged_expanded_v1.json"),
            ("ResembleAI/Chatterbox-Multilingual-pt-br", "t3_pt_br.safetensors"),
            ("ResembleAI/Chatterbox-Multilingual-pt-br", "s3gen_v3.pt"),
        ]
        for repo, filename in required:
            destination = model_dir / filename
            if not destination.exists():
                origin = Path(hf_hub_download(repo, filename, cache_dir=cache)).resolve()
                try:
                    destination.symlink_to(origin)
                except OSError:
                    os.link(origin, destination)
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.soundfile = soundfile
        self.model = package.ChatterboxTTS.from_local(model_dir, device)
        self._reference_path: Path | None = None
        if device == "cuda":
            import types

            def cpu_istft(generator, magnitude, phase):
                magnitude_cpu = torch.clamp(magnitude.float().cpu(), max=1e2)
                phase_cpu = phase.float().cpu()
                spectrum = torch.polar(magnitude_cpu, phase_cpu)
                audio_cpu = torch.istft(
                    spectrum, generator.istft_params["n_fft"],
                    generator.istft_params["hop_len"], generator.istft_params["n_fft"],
                    window=generator.stft_window.float().cpu(),
                )
                return audio_cpu.to(magnitude.device, dtype=magnitude.dtype)

            self.model.s3gen.mel2wav._istft = types.MethodType(cpu_istft, self.model.s3gen.mel2wav)
        original_flow = self.model.s3gen.flow.inference

        def guarded_flow(*args, **kwargs):
            # Some ROCm runs pass lengths that disagree with the actual token
            # tensors. The decoder mask must describe the tensors it receives.
            kwargs["token_len"] = torch.tensor(
                [kwargs["token"].shape[1]], device=kwargs["token"].device, dtype=torch.long
            )
            kwargs["prompt_token_len"] = torch.tensor(
                [kwargs["prompt_token"].shape[1]], device=kwargs["prompt_token"].device,
                dtype=torch.long,
            )
            return original_flow(*args, **kwargs)

        self.model.s3gen.flow.inference = guarded_flow


def _words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in normalized if not unicodedata.combining(char))
    return [word for word in re.findall(r"[a-z0-9]+", plain) if len(word) > 2]


class SpeechQualityCheck:
    """Catch gross omissions and repetitions with a small offline Portuguese ASR."""

    def __init__(self) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            "tiny", device="cpu", compute_type="int8",
            download_root=str(Path(__file__).resolve().parent / "data" / "asr"),
        )

    def check(self, expected: str, audio_path: Path) -> tuple[float, str]:
        segments, _ = self.model.transcribe(str(audio_path), language="pt", vad_filter=False)
        heard = " ".join(segment.text for segment in segments).strip()
        wanted = Counter(_words(expected))
        actual = Counter(_words(heard))
        if not wanted:
            return 1.0, heard
        matched = sum(min(count, actual[word]) for word, count in wanted.items())
        return matched / sum(wanted.values()), heard


def render_script(
    text: str,
    reference: Path,
    output: Path,
    work_root: Path,
    engine_factory: Callable[[], object] = ChatterboxEngine,
    engine_name: str = "chatterbox-multilingual-v3-200-qa",
    progress: Callable[[int, int, str], None] | None = None,
    stop: Event | None = None,
) -> Path:
    chunks = split_script(text)
    if not reference.is_file():
        raise FileNotFoundError(reference)
    identity = hashlib.sha256(
        json.dumps({"text": chunks, "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
                    "engine": engine_name,
                    "chunk_chars": CHUNK_CHARS},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    job = work_root / identity
    job.mkdir(parents=True, exist_ok=True)
    (job / "script.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    parts = [job / f"{i:05d}.wav" for i in range(len(chunks))]
    engine = None
    checker = None
    quality_enabled = isinstance(engine_factory, type) and issubclass(engine_factory, ChatterboxEngine)

    def reset_engine() -> None:
        nonlocal engine
        engine = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def render_one(chunk: str, part: Path, number: int, depth: int = 0) -> None:
        nonlocal engine, checker
        if _valid_wav(part):
            return
        part.unlink(missing_ok=True)
        last_error = ""
        for attempt in range(1, 4):
            if stop and stop.is_set():
                raise InterruptedError("Geração pausada. Execute novamente para retomar.")
            try:
                if engine is None:
                    engine = engine_factory()
                engine.render(chunk, reference, part)
                if not _valid_wav(part):
                    raise RuntimeError("O motor retornou áudio vazio.")
                if quality_enabled and len(_words(chunk)) >= 5:
                    if checker is None:
                        checker = SpeechQualityCheck()
                    score, heard = checker.check(chunk, part)
                    if score < 0.55:
                        os.replace(part, part.with_name(f"{part.stem}-revisar-{attempt}.wav"))
                        raise RuntimeError(f"A fala divergiu do roteiro. Foi ouvido: {heard}")
                return
            except (RuntimeError, AssertionError, ValueError) as error:
                last_error = str(error)
                part.unlink(missing_ok=True)
                LOGGER.warning("Trecho %s: tentativa %s falhou: %s", number, attempt, error)
                reset_engine()
        words = chunk.split()
        if depth < 2 and len(words) >= 10:
            midpoint = len(words) // 2
            left_text, right_text = " ".join(words[:midpoint]), " ".join(words[midpoint:])
            left = part.with_name(f"{part.stem}-a.wav")
            right = part.with_name(f"{part.stem}-b.wav")
            LOGGER.warning("Trecho %s: dividindo após três falhas.", number)
            render_one(left_text, left, number, depth + 1)
            render_one(right_text, right, number, depth + 1)
            combine_wavs([left, right], part)
            return
        raise RuntimeError(f"O trecho {number} falhou mesmo após dividir o texto: {last_error}")

    for i, (chunk, part) in enumerate(zip(chunks, parts), 1):
        if stop and stop.is_set():
            raise InterruptedError("Geração pausada. Execute novamente para retomar.")
        render_one(chunk, part, i)
        if progress:
            progress(i, len(chunks), str(part))
    combine_wavs(parts, output)
    return output
