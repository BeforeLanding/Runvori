"""Offline Host-local device administration under the Host instance lock."""

from pathlib import Path

from runvori.infrastructure.instance_lock import WindowsInstanceLock
from runvori.infrastructure.pairing_journal import PairingJournal


class LocalDeviceAdminError(ValueError):
    """Stable local refusal without path or credential material."""


def revoke_device_offline(data_dir: Path) -> dict[str, str]:
    """Revoke only while no Host owns this directory's instance lock."""
    if not isinstance(data_dir, Path) or not data_dir.is_absolute() or not data_dir.is_dir():
        raise LocalDeviceAdminError("invalid_host_data_dir")
    for segment in (data_dir, *data_dir.parents):
        if segment.is_symlink() or (hasattr(segment, "is_junction")
                                    and segment.is_junction()):
            raise LocalDeviceAdminError("unsafe_host_data_dir")
    with WindowsInstanceLock(data_dir / "host.lock"):
        pairing_db = data_dir / "pairing.db"
        if (not pairing_db.is_file() or pairing_db.is_symlink()
                or (hasattr(pairing_db, "is_junction")
                    and pairing_db.is_junction())):
            raise LocalDeviceAdminError("pairing_store_not_found")
        with PairingJournal(pairing_db) as journal:
            journal.revoke_local_device()
    return {"status": "passed", "device": "revoked"}
