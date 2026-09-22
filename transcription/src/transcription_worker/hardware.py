"""Hardware/device detection for the transcription worker.

Reports what's actually available on this machine so P2.1 smoke tests record
real device placement instead of assumptions. Run directly for a one-shot
report: `uv run --project transcription/ python -m transcription_worker.hardware`
"""

from __future__ import annotations

import platform
import subprocess  # nosec: fixed argv, no shell, no untrusted input


def cpu_info() -> str:
    # sysctl is the standard macOS way to read the CPU brand string.
    try:
        return subprocess.run(  # nosec: fixed argv, no shell
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return platform.processor() or "unknown"


def ram_gb() -> float:
    # sysctl hw.memsize returns total physical RAM in bytes.
    try:
        out = subprocess.run(  # nosec: fixed argv, no shell
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return round(int(out) / (1024**3), 1)
    except Exception:  # no sysctl / no hw.memsize off macOS: report unknown, don't crash
        return 0.0


def mlx_metal_available() -> str:
    # https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.metal.is_available.html
    try:
        import mlx.core as mx

        return str(mx.metal.is_available())
    except Exception as exc:  # not installed, or API moved
        return f"unavailable ({exc})"


def torch_mps_available() -> str:
    # https://docs.pytorch.org/docs/stable/backends.html#torch.backends.mps.is_available
    try:
        import torch

        return str(torch.backends.mps.is_available())
    except Exception as exc:
        return f"unavailable ({exc})"


def ctranslate2_devices() -> str:
    # https://opennmt.net/CTranslate2/python/ctranslate2.get_supported_compute_types.html
    try:
        import ctranslate2

        cpu_types = ctranslate2.get_supported_compute_types("cpu")
        try:
            cuda_types = ctranslate2.get_supported_compute_types("cuda")
        except Exception:
            cuda_types = set()
        try:
            mps_types = ctranslate2.get_supported_compute_types("mps")
        except Exception:
            mps_types = set()
        return f"cpu={sorted(cpu_types)} cuda={sorted(cuda_types)} mps={sorted(mps_types)}"
    except Exception as exc:
        return f"unavailable ({exc})"


def report() -> dict[str, str]:
    return {
        "cpu": cpu_info(),
        "ram_gb": str(ram_gb()),
        "platform": platform.platform(),
        "mlx_metal_available": mlx_metal_available(),
        "torch_mps_available": torch_mps_available(),
        "ctranslate2_devices": ctranslate2_devices(),
    }


if __name__ == "__main__":
    for key, value in report().items():
        print(f"{key}: {value}")
