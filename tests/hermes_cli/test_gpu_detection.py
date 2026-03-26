"""Tests for GPU hardware detection and vLLM config generation.

Covers: detect_local_gpu(), generate_vllm_config(), run_local_gpu_setup().
"""
import sys
from unittest.mock import patch, MagicMock

import pytest

from hermes_cli.setup import (
    _parse_nvidia_gpus,
    _parse_amd_gpus,
    _parse_apple_silicon,
    detect_local_gpu,
    generate_vllm_config,
    run_local_gpu_setup,
)


# ---------------------------------------------------------------------------
# _parse_nvidia_gpus
# ---------------------------------------------------------------------------

def test_parse_nvidia_gpus_no_smi(monkeypatch):
    """Returns empty list when nvidia-smi is not in PATH."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert _parse_nvidia_gpus() == []


def test_parse_nvidia_gpus_single(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/nvidia-smi" if cmd == "nvidia-smi" else None)
    fake_output = "NVIDIA GeForce RTX 3090, 24576, 535.54.03\n"
    with patch("hermes_cli.setup._run_silent", return_value=fake_output):
        gpus = _parse_nvidia_gpus()
    assert len(gpus) == 1
    assert gpus[0]["vendor"] == "nvidia"
    assert gpus[0]["name"] == "NVIDIA GeForce RTX 3090"
    assert gpus[0]["vram_mb"] == 24576
    assert gpus[0]["driver"] == "535.54.03"


def test_parse_nvidia_gpus_multi(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/nvidia-smi" if cmd == "nvidia-smi" else None)
    fake_output = (
        "NVIDIA A100-SXM4-80GB, 81920, 520.61.05\n"
        "NVIDIA A100-SXM4-80GB, 81920, 520.61.05\n"
    )
    with patch("hermes_cli.setup._run_silent", return_value=fake_output):
        gpus = _parse_nvidia_gpus()
    assert len(gpus) == 2
    assert all(g["vendor"] == "nvidia" for g in gpus)


def test_parse_nvidia_gpus_malformed_line(monkeypatch):
    """Lines with fewer than 3 fields are skipped without error."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/nvidia-smi" if cmd == "nvidia-smi" else None)
    with patch("hermes_cli.setup._run_silent", return_value="bad line\n"):
        gpus = _parse_nvidia_gpus()
    assert gpus == []


# ---------------------------------------------------------------------------
# _parse_amd_gpus
# ---------------------------------------------------------------------------

def test_parse_amd_gpus_no_rocmsmi(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert _parse_amd_gpus() == []


def test_parse_amd_gpus_single(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/rocm-smi" if cmd == "rocm-smi" else None)
    # rocm-smi csv: first line is a header starting with "GPU" (skipped), data rows start with index
    fake_output = "GPU,VRAM Total(MiB),VRAM Used(MiB)\n0, 16384, 1024\n"
    with patch("hermes_cli.setup._run_silent", return_value=fake_output):
        gpus = _parse_amd_gpus()
    assert len(gpus) == 1
    assert gpus[0]["vendor"] == "amd"
    assert gpus[0]["vram_mb"] == 16384


# ---------------------------------------------------------------------------
# _parse_apple_silicon
# ---------------------------------------------------------------------------

def test_parse_apple_silicon_non_darwin(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert _parse_apple_silicon() == []


def test_parse_apple_silicon_darwin(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    def fake_run_silent(cmd):
        if cmd == ["sysctl", "-n", "machdep.cpu.brand_string"]:
            return "Apple M2 Pro\n"
        if cmd == ["sysctl", "-n", "hw.memsize"]:
            return str(32 * 1024 * 1024 * 1024)  # 32 GB
        return ""

    with patch("hermes_cli.setup._run_silent", side_effect=fake_run_silent):
        gpus = _parse_apple_silicon()

    assert len(gpus) == 1
    assert gpus[0]["vendor"] == "apple"
    assert gpus[0]["driver"] == "Metal"
    assert gpus[0]["vram_mb"] == 32 * 1024  # 32768 MB


# ---------------------------------------------------------------------------
# detect_local_gpu — priority order
# ---------------------------------------------------------------------------

def test_detect_local_gpu_prefers_nvidia_over_amd(monkeypatch):
    nvidia_gpu = [{"vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24576, "driver": "545"}]
    amd_gpu = [{"vendor": "amd", "name": "AMD GPU 0", "vram_mb": 16384, "driver": "ROCm"}]

    with patch("hermes_cli.setup._parse_nvidia_gpus", return_value=nvidia_gpu), \
         patch("hermes_cli.setup._parse_amd_gpus", return_value=amd_gpu):
        result = detect_local_gpu()

    assert result == nvidia_gpu


def test_detect_local_gpu_falls_back_to_amd(monkeypatch):
    amd_gpu = [{"vendor": "amd", "name": "AMD GPU 0", "vram_mb": 16384, "driver": "ROCm"}]

    with patch("hermes_cli.setup._parse_nvidia_gpus", return_value=[]), \
         patch("hermes_cli.setup._parse_amd_gpus", return_value=amd_gpu):
        result = detect_local_gpu()

    assert result == amd_gpu


def test_detect_local_gpu_no_gpu():
    with patch("hermes_cli.setup._parse_nvidia_gpus", return_value=[]), \
         patch("hermes_cli.setup._parse_amd_gpus", return_value=[]), \
         patch("hermes_cli.setup._parse_apple_silicon", return_value=[]):
        result = detect_local_gpu()

    assert result == []


# ---------------------------------------------------------------------------
# generate_vllm_config
# ---------------------------------------------------------------------------

def test_generate_vllm_config_empty():
    assert generate_vllm_config([]) == {}


def test_generate_vllm_config_single_nvidia():
    gpus = [{"vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24576, "driver": "545"}]
    cfg = generate_vllm_config(gpus, "NousResearch/Hermes-3-Llama-3.1-8B")

    assert cfg["tensor_parallel_size"] == 1
    assert cfg["gpu_memory_utilization"] == 0.90
    assert cfg["dtype"] == "bfloat16"
    assert cfg["max_model_len"] == 16384
    assert "vllm serve" in cfg["command"]
    assert "NousResearch/Hermes-3-Llama-3.1-8B" in cfg["command"]
    assert "--tensor-parallel-size 1" in cfg["command"]
    assert cfg["base_url"] == "http://localhost:8000/v1"


def test_generate_vllm_config_multi_gpu_high_vram():
    gpus = [
        {"vendor": "nvidia", "name": "A100", "vram_mb": 81920, "driver": "520"},
        {"vendor": "nvidia", "name": "A100", "vram_mb": 81920, "driver": "520"},
    ]
    cfg = generate_vllm_config(gpus)

    assert cfg["tensor_parallel_size"] == 2
    assert cfg["max_model_len"] is None  # enough VRAM — auto-detect
    assert "--tensor-parallel-size 2" in cfg["command"]
    assert "--max-model-len" not in cfg["command"]


def test_generate_vllm_config_low_vram():
    gpus = [{"vendor": "nvidia", "name": "GTX 1060", "vram_mb": 6144, "driver": "525"}]
    cfg = generate_vllm_config(gpus)

    assert cfg["max_model_len"] == 4096
    assert "--max-model-len 4096" in cfg["command"]


def test_generate_vllm_config_amd_uses_float16():
    gpus = [{"vendor": "amd", "name": "RX 7900 XTX", "vram_mb": 24576, "driver": "ROCm"}]
    cfg = generate_vllm_config(gpus)
    assert cfg["dtype"] == "float16"


def test_generate_vllm_config_apple_lower_utilization():
    gpus = [{"vendor": "apple", "name": "Apple M2 Max", "vram_mb": 32768, "driver": "Metal"}]
    cfg = generate_vllm_config(gpus)
    assert cfg["gpu_memory_utilization"] == 0.85


def test_generate_vllm_config_placeholder_when_no_model():
    gpus = [{"vendor": "nvidia", "name": "RTX 3080", "vram_mb": 10240, "driver": "525"}]
    cfg = generate_vllm_config(gpus)
    assert "YOUR_MODEL_ID" in cfg["command"]


# ---------------------------------------------------------------------------
# run_local_gpu_setup — smoke test (no real hardware calls)
# ---------------------------------------------------------------------------

def test_run_local_gpu_setup_no_gpu(capsys):
    with patch("hermes_cli.setup.detect_local_gpu", return_value=[]):
        run_local_gpu_setup()
    out = capsys.readouterr().out
    assert "No GPU detected" in out


def test_run_local_gpu_setup_with_gpu(capsys, monkeypatch):
    gpus = [{"vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24576, "driver": "545"}]
    # Non-interactive: suppress stdin.isatty check
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))

    with patch("hermes_cli.setup.detect_local_gpu", return_value=gpus):
        run_local_gpu_setup()

    out = capsys.readouterr().out
    assert "RTX 4090" in out
    assert "vllm serve" in out
    assert "http://localhost:8000/v1" in out


def test_run_local_gpu_setup_low_vram_suggests_ollama(capsys, monkeypatch):
    gpus = [{"vendor": "nvidia", "name": "GTX 1060", "vram_mb": 6144, "driver": "525"}]
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))

    with patch("hermes_cli.setup.detect_local_gpu", return_value=gpus):
        run_local_gpu_setup()

    out = capsys.readouterr().out
    assert "ollama" in out.lower() or "Ollama" in out
