"""Small offline desktop interface for long-form voice cloning."""

from __future__ import annotations

import threading
import tkinter as tk
import json
import os
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pipeline import (BrazilianChatterboxEngine, ChatterboxEngine, prepare_reference,
                      render_script, split_script)

HERE = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(HERE / "data" / "hf"))


def saved_settings() -> dict:
    settings = HERE / "data" / "config.json"
    if settings.is_file():
        try:
            return json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def ffmpeg_path() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Voz Local — narração sem limite de roteiro")
        self.geometry("900x720")
        self.minsize(700, 550)
        self.configure(bg="#101820")
        settings = saved_settings()
        self.reference = tk.StringVar(value=str(settings.get("reference", "")))
        self.engine = tk.StringVar(value=str(settings.get("engine", "Multilíngue V3")))
        self.destination = tk.StringVar(value=str(HERE / "renders" / "narracao.wav"))
        self.status = tk.StringVar(value="Escolha sua gravação e escreva ou importe um roteiro.")
        self.stop_event = threading.Event()
        self._build()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#101820")
        style.configure("TLabel", background="#101820", foreground="#e8edf2", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 10), padding=8)
        style.configure("TEntry", padding=7)
        style.configure("TProgressbar", background="#3ecf9a", troughcolor="#263640")
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Voz Local", font=("Segoe UI Semibold", 23)).pack(anchor="w")
        ttk.Label(frame, text="Sua voz, seus roteiros, processamento no seu computador.").pack(anchor="w", pady=(2, 20))

        ttk.Label(frame, text="Gravação de referência (M4A, MP3 ou WAV)").pack(anchor="w")
        ref_row = ttk.Frame(frame)
        ref_row.pack(fill="x", pady=(4, 14))
        ttk.Entry(ref_row, textvariable=self.reference).pack(side="left", fill="x", expand=True)
        ttk.Button(ref_row, text="Escolher", command=self._choose_reference).pack(side="left", padx=(8, 0))

        ttk.Label(frame, text="Motor de voz").pack(anchor="w")
        engine_picker = ttk.Combobox(frame, textvariable=self.engine, state="readonly",
                                     values=("Multilíngue V3", "Português brasileiro V3"))
        engine_picker.pack(fill="x", pady=(4, 14))

        title_row = ttk.Frame(frame)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Roteiro").pack(side="left")
        ttk.Button(title_row, text="Importar TXT", command=self._import_text).pack(side="right")
        self.editor = tk.Text(frame, wrap="word", font=("Segoe UI", 12), bg="#18242e", fg="#f5f7fa",
                              insertbackground="#f5f7fa", relief="flat", padx=14, pady=12,
                              undo=True, highlightthickness=2, highlightcolor="#3ecf9a")
        self.editor.pack(fill="both", expand=True, pady=(6, 14))

        ttk.Label(frame, text="Salvar narração em").pack(anchor="w")
        out_row = ttk.Frame(frame)
        out_row.pack(fill="x", pady=(4, 14))
        ttk.Entry(out_row, textvariable=self.destination).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="Escolher", command=self._choose_output).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Gerar / retomar", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Pausar após este trecho", command=self.stop_event.set,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill="x", pady=(14, 7))
        ttk.Label(frame, textvariable=self.status, wraplength=830).pack(anchor="w")

    def _choose_reference(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Áudio", "*.m4a *.mp3 *.wav *.flac *.ogg"), ("Todos", "*.*")])
        if path:
            self.reference.set(path)

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV", "*.wav")])
        if path:
            self.destination.set(path)

    def _import_text(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Texto", "*.txt"), ("Todos", "*.*")])
        if path:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", Path(path).read_text(encoding="utf-8-sig"))

    def _start(self) -> None:
        source = Path(self.reference.get())
        text = self.editor.get("1.0", "end").strip()
        target = Path(self.destination.get())
        engine_choice = self.engine.get()
        try:
            chunks = split_script(text)
            if not source.is_file():
                raise ValueError("Selecione uma gravação existente.")
            if target.suffix.lower() != ".wav":
                raise ValueError("O arquivo de saída precisa terminar em .wav.")
        except ValueError as error:
            messagebox.showerror("Verifique os campos", str(error))
            return
        self.stop_event.clear()
        settings = HERE / "data" / "config.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"reference": str(source), "engine": engine_choice},
                                       ensure_ascii=False), encoding="utf-8")
        self.progress.configure(maximum=len(chunks), value=0)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Preparando a referência...")

        def worker() -> None:
            try:
                reference = prepare_reference(source, HERE / "data" / "reference.wav", ffmpeg_path())
                self.after(0, self.status.set, "Carregando o modelo e gerando. O primeiro trecho pode demorar.")
                factory = (BrazilianChatterboxEngine if engine_choice == "Português brasileiro V3"
                           else ChatterboxEngine)
                engine_id = "chatterbox-ptbr-v3-200-qa" if factory is BrazilianChatterboxEngine else "chatterbox-v3-200-qa"
                output = render_script(text, reference, target, HERE / "renders" / "parts",
                                       engine_factory=factory, engine_name=engine_id,
                                       progress=lambda done, total, _: self.after(0, self._show_progress, done, total),
                                       stop=self.stop_event)
                self.after(0, self._finish, f"Pronto: {output}")
            except Exception as error:
                log = HERE / "data" / "last-error.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(traceback.format_exc(), encoding="utf-8")
                self.after(0, self._finish, f"Falha: {error}. Detalhes: {log}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.configure(value=done)
        self.status.set(f"Trecho {done} de {total} salvo. Se interromper, os trechos prontos serão reutilizados.")

    def _finish(self, result: str) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set(result)


if __name__ == "__main__":
    App().mainloop()
