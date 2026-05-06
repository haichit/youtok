import hashlib
import platform
import subprocess


def get_machine_id() -> str:
    sys = platform.system()
    if sys == "Darwin":
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            text=True,
            encoding="utf-8",
        )
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                uuid = line.split('"')[3]
                return hashlib.sha256(uuid.encode()).hexdigest()[:16]
    elif sys == "Windows":
        out = subprocess.check_output(
            ["wmic", "csproduct", "get", "UUID"],
            text=True,
            encoding="utf-8",
        )
        # wmic on Win11 inserts a blank line between the "UUID" header
        # and the value; pick the first non-header non-empty line.
        uuid = ""
        for line in out.splitlines():
            line = line.strip()
            if line and line.upper() != "UUID":
                uuid = line
                break
        if not uuid:
            raise RuntimeError("Could not read machine UUID from wmic")
        return hashlib.sha256(uuid.encode()).hexdigest()[:16]
    raise RuntimeError(f"Unsupported platform: {sys}")
