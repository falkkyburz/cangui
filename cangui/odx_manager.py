from dataclasses import dataclass, field
from pathlib import Path

import odxtools


@dataclass
class OdxDid:
    did_id: int
    name: str
    description: str = ""
    byte_size: int = 0


@dataclass
class OdxService:
    short_name: str
    long_name: str = ""
    service_id: int = 0
    description: str = ""


@dataclass
class OdxVariant:
    short_name: str
    long_name: str = ""
    dids: list[OdxDid] = field(default_factory=list)
    services: list[OdxService] = field(default_factory=list)


class OdxManager:
    """Manages ODX/PDX diagnostic database files using odxtools."""

    def __init__(self):
        self._databases: list[odxtools.database.Database] = []
        self._files: list[Path] = []
        self._variants: list[OdxVariant] = []

    @property
    def files(self) -> list[Path]:
        return list(self._files)

    @property
    def variants(self) -> list[OdxVariant]:
        return self._variants

    def load_file(self, path: str | Path) -> list[str]:
        """Load an ODX/PDX file. Returns list of variant short names."""
        path = Path(path)
        if path in self._files:
            return []
        db = odxtools.load_file(path)
        self._databases.append(db)
        self._files.append(path)
        names = self._extract_variants(db)
        return names

    def _extract_variants(self, db: odxtools.database.Database) -> list[str]:
        names = []
        for dl in db.diag_layers:
            variant = OdxVariant(
                short_name=dl.short_name,
                long_name=getattr(dl, "long_name", "") or "",
            )
            # Extract DIDs from diag services
            try:
                for service in dl.services:
                    odx_svc = OdxService(
                        short_name=service.short_name,
                        long_name=getattr(service, "long_name", "") or "",
                    )
                    # Try to get service ID from request
                    if hasattr(service, "request") and service.request is not None:
                        req = service.request
                        if hasattr(req, "coded_const_prefix"):
                            prefix = req.coded_const_prefix()
                            if prefix:
                                odx_svc.service_id = prefix[0]
                    variant.services.append(odx_svc)

                    # Extract DID info from ReadDataByIdentifier services
                    self._extract_dids_from_service(service, variant)
            except Exception:
                pass

            self._variants.append(variant)
            names.append(variant.short_name)
        return names

    def _extract_dids_from_service(self, service, variant: OdxVariant):
        """Try to extract DID definitions from a service."""
        try:
            if not hasattr(service, "request") or service.request is None:
                return
            req = service.request
            prefix = req.coded_const_prefix()
            if not prefix or prefix[0] != 0x22:
                return
            # This is a ReadDataByIdentifier — try to get the DID
            if len(prefix) >= 3:
                did_id = (prefix[1] << 8) | prefix[2]
                did = OdxDid(
                    did_id=did_id,
                    name=service.short_name,
                    description=getattr(service, "long_name", "") or "",
                )
                # Avoid duplicates
                if not any(d.did_id == did_id for d in variant.dids):
                    variant.dids.append(did)
        except Exception:
            pass

    def get_all_dids(self) -> list[OdxDid]:
        """Get all DIDs across all loaded variants."""
        seen: set[int] = set()
        result: list[OdxDid] = []
        for v in self._variants:
            for d in v.dids:
                if d.did_id not in seen:
                    seen.add(d.did_id)
                    result.append(d)
        result.sort(key=lambda d: d.did_id)
        return result

    def remove_file(self, path: str | Path):
        path = Path(path)
        if path not in self._files:
            return
        idx = self._files.index(path)
        self._files.pop(idx)
        self._databases.pop(idx)
        # Rebuild variants
        self._variants.clear()
        for db in self._databases:
            self._extract_variants(db)

    def clear(self):
        self._databases.clear()
        self._files.clear()
        self._variants.clear()
