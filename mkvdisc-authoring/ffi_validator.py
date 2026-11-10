from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path


class CValidationResult(ctypes.Structure):
    _fields_ = [
        ("is_valid", ctypes.c_bool),
        ("error_message", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    error_message: str | None


class ManifestValidationError(RuntimeError):
    pass


class FfiValidator:
    def __init__(self, library_path: str | Path | None = None) -> None:
        configured = library_path or os.environ.get("MKVDISC_DLL")
        if configured:
            path = Path(configured).expanduser().resolve()
        else:
            path = (
                Path(__file__).resolve().parent.parent
                / "target"
                / "release"
                / "mkvdisc.dll"
            )
        if not path.is_file():
            raise FileNotFoundError(
                f"Rust validator DLL not found at {path}. "
                "Build it with `cargo build --release -p mkvdisc` or set MKVDISC_DLL."
            )

        self._library = ctypes.CDLL(str(path))
        self._validate = self._library.mkvdisc_validate_manifest
        self._validate.argtypes = [ctypes.c_char_p]
        self._validate.restype = CValidationResult
        self._free = self._library.mkvdisc_free_validation_result
        self._free.argtypes = [CValidationResult]
        self._free.restype = None

    def validate(self, manifest_json: str) -> ValidationResult:
        encoded = manifest_json.encode("utf-8")
        if b"\0" in encoded:
            raise ValueError("Manifest JSON contains a NUL byte.")

        result = self._validate(encoded)
        try:
            validation = ValidationResult(
                valid=bool(result.is_valid),
                error_message=_decode(result.error_message),
            )
        finally:
            self._free(result)

        if not validation.valid:
            raise ManifestValidationError(
                validation.error_message or "Manifest validation failed."
            )
        return validation


def _decode(pointer: int | None) -> str | None:
    if not pointer:
        return None
    return ctypes.string_at(pointer).decode("utf-8", errors="replace")
