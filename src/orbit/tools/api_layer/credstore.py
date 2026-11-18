"""Secrets in the Windows Credential Manager (the OS credential store). Nothing secret touches disk files or logs."""
import ctypes
from ctypes import wintypes

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
_advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredFree.argtypes = [ctypes.c_void_p]


def save_secret(target: str, value: str, user: str = "orbit") -> None:
    blob = value.encode("utf-16-le")
    if len(blob) > 2560:
        raise ValueError("secret too large for the Windows credential store (2560 bytes)")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIAL()
    cred.Type = _CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.UserName = user
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
    if not _advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f"could not save to the Windows credential store (error {ctypes.get_last_error()})")


def load_secret(target: str) -> str | None:
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if not _advapi32.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        cred = pcred.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        _advapi32.CredFree(pcred)


def delete_secret(target: str) -> bool:
    return bool(_advapi32.CredDeleteW(target, _CRED_TYPE_GENERIC, 0))
