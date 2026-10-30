"""zstd-Kompression für ERP-Fragmente.

Bevorzugt das Python-Paket ``zstandard`` (pip install zstandard).
Ist es nicht installiert, wird das Kommandozeilenprogramm ``zstd`` verwendet.
"""

import shutil
import subprocess

try:  # pragma: no cover - abhängig von der Umgebung
    import zstandard as _zstd
except ImportError:  # pragma: no cover
    _zstd = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
DEFAULT_LEVEL = 19


class CompressionError(RuntimeError):
    pass


def backend_name() -> str:
    if _zstd is not None:
        return "zstandard %s (Python)" % _zstd.__version__
    if shutil.which("zstd"):
        return "zstd (Kommandozeile)"
    return "keins"


def decompress(data: bytes, expected_size: int) -> bytes:
    """Entpackt einen zstd-Frame und prüft die erwartete Größe."""
    if not data.startswith(ZSTD_MAGIC):
        raise CompressionError("Kein zstd-Frame (Magic fehlt)")
    if _zstd is not None:
        out = _zstd.ZstdDecompressor().decompress(data, max_output_size=max(expected_size, 1))
    else:
        out = _run_cli(["zstd", "-d", "-q", "-c"], data)
    if len(out) != expected_size:
        raise CompressionError(
            "Entpackte Größe %d stimmt nicht mit erwarteter Größe %d überein" % (len(out), expected_size)
        )
    return out


def compress(data: bytes, level: int = DEFAULT_LEVEL) -> bytes:
    """Komprimiert zu einem Standard-zstd-Frame (mit Inhaltsgröße, ohne Prüfsumme),
    so wie es auch die Originaldateien des Spiels enthalten."""
    if _zstd is not None:
        c = _zstd.ZstdCompressor(level=level, write_content_size=True, write_checksum=False)
        return c.compress(data)
    return _run_cli(["zstd", "-q", "-c", "--no-check", "-%d" % min(max(level, 1), 19)], data)


def _run_cli(cmd, data: bytes) -> bytes:
    if not shutil.which(cmd[0]):
        raise CompressionError(
            "Weder das Python-Paket 'zstandard' noch das Programm 'zstd' ist verfügbar. "
            "Installation: pip install zstandard"
        )
    p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise CompressionError(p.stderr.decode("utf-8", "replace").strip() or "zstd-Fehler")
    return p.stdout
