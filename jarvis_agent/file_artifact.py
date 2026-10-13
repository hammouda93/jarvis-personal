"""Independent bounded local-file evidence; never writes or saves a document."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


def _long_lexical_path(path):
    absolute = str(path.absolute())
    if os.name != "nt":
        return absolute
    import ctypes
    from ctypes import wintypes
    expand = ctypes.WinDLL("kernel32", use_last_error=True).GetLongPathNameW
    expand.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    expand.restype = wintypes.DWORD
    size = expand(absolute, None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    if size > 32768:
        raise ValueError("artifact_path_exceeds_budget")
    buffer = ctypes.create_unicode_buffer(size)
    copied = expand(absolute, buffer, size)
    if not copied:
        raise ctypes.WinError(ctypes.get_last_error())
    if copied >= size:
        raise ValueError("artifact_path_changed_during_expansion")
    return buffer.value


def verify_file_artifact(arguments):
    result = {"verified": False, "read_only": True, "postcondition": "file_artifact_matches"}
    try:
        if set(arguments) - {"path", "expected_name", "expected_text", "expected_sha256"}:
            raise ValueError("unsupported_artifact_argument")
        raw, name = arguments.get("path"), arguments.get("expected_name")
        if not isinstance(raw, str) or not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("explicit_path_and_filename_required")
        path = Path(raw)
        if not path.is_absolute() or raw.startswith(("\\\\", "//")):
            raise ValueError("local_absolute_path_required")
        # Check root-to-leaf without following a redirected ancestor.
        for component in reversed((path, *path.parents)):
            metadata = component.lstat()
            if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise ValueError("redirected_artifact_path_refused")
        resolved = path.resolve(strict=True)
        # Expand Windows 8.3 names, but do not resolve links in the lexical path.
        if str(resolved).startswith(("\\\\", "//")) or os.path.normcase(str(resolved)) != os.path.normcase(_long_lexical_path(path)):
            raise ValueError("redirected_artifact_path_refused")
        if resolved.name != name or not resolved.is_file():
            raise ValueError("artifact_filename_mismatch")
        text, digest = arguments.get("expected_text"), arguments.get("expected_sha256")
        if text is None and digest is None:
            raise ValueError("expected_content_required")
        if text is not None and (not isinstance(text, str) or len(text.encode("utf-8")) > MAX_ARTIFACT_BYTES):
            raise ValueError("invalid_expected_text")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch("[a-fA-F0-9]{64}", digest)):
            raise ValueError("invalid_expected_sha256")
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact_exceeds_read_budget")
            data = handle.read(MAX_ARTIFACT_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = resolved.stat()
        stamp = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if len(data) > MAX_ARTIFACT_BYTES or stamp(before) != stamp(after) or stamp(after) != stamp(current):
            raise ValueError("artifact_changed_during_verification")
        actual_hash = hashlib.sha256(data).hexdigest()
        if digest is not None and actual_hash != digest.lower():
            raise ValueError("artifact_hash_mismatch")
        if text is not None and data.decode("utf-8-sig").replace("\r\n", "\n") != text.replace("\r\n", "\n"):
            raise ValueError("artifact_content_mismatch")
        result.update(verified=True, path=str(resolved), name=resolved.name,
                      bytes=len(data), sha256=actual_hash, content_match=True)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        # Do not leak file contents, OS bodies or paths from an error message.
        result["error"] = str(exc) if type(exc) is ValueError else type(exc).__name__
    return result


def guard_saved_file_answer(user_text, answer, actions):
    from .instruction_clauses import positive_instruction_clauses
    requested = any(re.search(r"\b(?:enregistre|enregistrer|sauvegarde|sauvegarder|save)\b", clause)
                    and not re.search(r"\b(?:en\s+|dans\s+(?:(?:la|ma)\s+)?|in\s+(?:the\s+)?)(?:memoire|memory)\b", clause)
                    for clause in positive_instruction_clauses(user_text))
    claim = any(re.search(r"\b(?:enregistre|sauvegarde|saved|fait|termine)\b", clause)
                for clause in positive_instruction_clauses(answer))
    if not requested or not claim:
        return answer, False
    for action in actions:
        if action.name != "verify_file_artifact" or not action.success:
            continue
        try:
            payload = json.loads(action.detail)
            if isinstance(payload, dict) and payload.get("verified") is True and payload.get("content_match") is True:
                return answer, False
        except (ValueError, TypeError):
            pass
    return ("Je n'ai pas de preuve independante du fichier enregistre avec le nom, le chemin et le contenu attendus. L'enregistrement reste a verifier.", True)
