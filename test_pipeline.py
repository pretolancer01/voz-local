import tempfile
import unittest
import wave
from pathlib import Path

from pipeline import CHUNK_CHARS, combine_wavs, render_script, split_script


def wav(path: Path, frames: int = 2400) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\x01\x00" * frames)


class FakeEngine:
    renders = 0

    def render(self, text: str, reference: Path, output: Path) -> None:
        self.__class__.renders += 1
        wav(output)


class FailOnceEngine(FakeEngine):
    fail_at = 3

    def render(self, text: str, reference: Path, output: Path) -> None:
        if self.__class__.fail_at == 3 and self.__class__.renders == 2:
            self.__class__.fail_at = -1
            raise RuntimeError("falha simulada")
        super().render(text, reference, output)


class FailLongEngine(FakeEngine):
    def render(self, text: str, reference: Path, output: Path) -> None:
        if len(text) > 100:
            raise RuntimeError("trecho longo rejeitado")
        super().render(text, reference, output)


class PipelineTests(unittest.TestCase):
    def test_long_script_resumes_without_missing_chunks(self) -> None:
        script = "Esta é uma frase longa para narrar o vídeo. " * 120
        chunks = split_script(script)
        self.assertGreater(len(chunks), 10)
        self.assertTrue(all(0 < len(chunk) <= CHUNK_CHARS for chunk in chunks))
        self.assertEqual(" ".join(chunks), " ".join(script.split()))
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            ref = root / "ref.wav"
            wav(ref)
            out = root / "final.wav"
            FakeEngine.renders = 0
            render_script(script, ref, out, root / "parts", engine_factory=FakeEngine)
            self.assertEqual(FakeEngine.renders, len(chunks))
            with wave.open(str(out), "rb") as result:
                self.assertGreater(result.getnframes(), 2400 * len(chunks))
            render_script(script, ref, out, root / "parts", engine_factory=FakeEngine)
            self.assertEqual(FakeEngine.renders, len(chunks))

    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_script("   ")

    def test_large_output_splits_at_wav_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            parts = [root / f"{i}.wav" for i in range(3)]
            for part in parts:
                wav(part)
            volumes = combine_wavs(parts, root / "book.wav", max_data_bytes=5000)
            self.assertEqual(len(volumes), 3)
            self.assertTrue(all(path.is_file() for path in volumes))
            self.assertTrue((root / "book.volumes.json").is_file())

    def test_failed_chunk_retries_without_losing_progress(self) -> None:
        script = "Narração de teste para retomar. " * 40
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            ref = root / "ref.wav"
            wav(ref)
            FailOnceEngine.renders = 0
            FailOnceEngine.fail_at = 3
            render_script(script, ref, root / "out.wav", root / "parts", engine_factory=FailOnceEngine)
            self.assertEqual(FailOnceEngine.renders, len(split_script(script)))
            self.assertTrue((root / "out.wav").is_file())

    def test_failed_long_chunk_is_split_and_completed(self) -> None:
        script = "Uma frase de teste com palavras suficientes para simular falhas do modelo e permitir a divisão automática. " * 3
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            ref = root / "ref.wav"
            wav(ref)
            out = root / "out.wav"
            FailLongEngine.renders = 0
            render_script(script, ref, out, root / "parts", engine_factory=FailLongEngine)
            self.assertTrue(out.is_file())
            self.assertGreater(FailLongEngine.renders, len(split_script(script)))
            with wave.open(str(out), "rb") as result:
                self.assertGreater(result.getnframes(), 0)


if __name__ == "__main__":
    unittest.main()
