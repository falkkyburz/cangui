"""ODX and PDX diagnostic database manager using odxtools.

This module provides the :class:`OdxManager` class together with three small
dataclasses — :class:`OdxDid`, :class:`OdxService`, and :class:`OdxVariant`
— that hold a simplified view of an ODX diagnostic layer extracted from an
ODX/PDX file parsed by odxtools.

ODX (Open Diagnostic Data Exchange) files describe UDS-based (ISO 14229)
diagnostic communication for ECUs.  The manager focuses on two aspects most
relevant to cangui:

- **Services**: every ``DiagService`` entry in a diagnostic layer, with its
  UDS service identifier byte extracted from the request's constant prefix.
- **DIDs**: ``ReadDataByIdentifier`` (UDS service 0x22) entries, with the
  2-byte DID identifier reconstructed from the request prefix bytes.

All parse errors encountered during variant/DID extraction are silently
suppressed because odxtools' API is highly version-dependent and optional
fields may be absent in some ODX dialects.
"""

from dataclasses import dataclass, field
from pathlib import Path

import odxtools


@dataclass
class OdxDid:
    """A UDS Data Identifier (DID) extracted from an ODX service definition.

    Represents a single ``ReadDataByIdentifier`` (service 0x22) entry found
    within a diagnostic layer.  Only DIDs whose 2-byte identifier can be
    unambiguously reconstructed from the request's constant prefix are
    included.

    Attributes:
        did_id: 16-bit DID identifier (e.g. ``0xF190`` for VIN).
        name: Short name of the originating ODX diagnostic service.
        description: Human-readable long name from the ODX service, if present.
            Empty string when not defined.
        byte_size: Expected response payload size in bytes.  Defaults to
            ``0`` when not determined from the ODX structure.
    """

    did_id: int
    name: str
    description: str = ""
    byte_size: int = 0


@dataclass
class OdxService:
    """A UDS diagnostic service extracted from an ODX diagnostic layer.

    Represents a single ``DiagService`` entry.  The ``service_id`` is the
    first byte of the UDS request (e.g. ``0x22`` for ReadDataByIdentifier,
    ``0x10`` for DiagnosticSessionControl).

    Attributes:
        short_name: Short name of the service as defined in the ODX file.
        long_name: Human-readable long name, or empty string if not present.
        service_id: UDS service identifier byte extracted from the request's
            constant prefix.  ``0`` when the prefix could not be determined.
        description: Additional free-text description from the ODX file.
            Empty string when not present.
    """

    short_name: str
    long_name: str = ""
    service_id: int = 0
    description: str = ""


@dataclass
class OdxVariant:
    """A logical ECU variant parsed from a single ODX diagnostic layer.

    Each ``DiagLayer`` in an ODX/PDX file corresponds to one
    :class:`OdxVariant`.  The variant aggregates the services and DIDs
    defined directly in that layer; inherited definitions from base layers
    are not currently expanded.

    Attributes:
        short_name: Short name of the diagnostic layer (e.g. ``"MyECU"``).
        long_name: Human-readable long name of the diagnostic layer, or empty
            string if absent.
        dids: List of :class:`OdxDid` entries found in
            ``ReadDataByIdentifier`` services within this layer.
        services: List of all :class:`OdxService` entries found in this layer.
    """

    short_name: str
    long_name: str = ""
    dids: list[OdxDid] = field(default_factory=list)
    services: list[OdxService] = field(default_factory=list)


class OdxManager:
    """Manages ODX/PDX diagnostic database files using odxtools.

    Loads one or more ODX/PDX files and extracts a simplified representation
    of their diagnostic layers as :class:`OdxVariant` objects.  The manager
    supports incremental loading and removal; removal triggers a full
    reconstruction of the variant list from the remaining databases.

    This class is **not** thread-safe.  All load/remove operations should be
    performed on the Qt main thread.
    """

    def __init__(self):
        """Initialise the manager with empty database and variant collections."""
        self._databases: list[odxtools.database.Database] = []
        self._files: list[Path] = []
        self._variants: list[OdxVariant] = []

    @property
    def files(self) -> list[Path]:
        """Snapshot list of all currently loaded ODX/PDX file paths.

        Returns:
            A new list of :class:`~pathlib.Path` objects for each file that
            has been successfully loaded.  Mutations do not affect internal
            state.
        """
        return list(self._files)

    @property
    def variants(self) -> list[OdxVariant]:
        """All ECU variants extracted from the currently loaded databases.

        Returns:
            The live internal list of :class:`OdxVariant` objects.  Do not
            modify the returned list directly.
        """
        return self._variants

    def load_file(self, path: str | Path) -> list[str]:
        """Load an ODX or PDX file and extract its diagnostic variant names.

        If the file is already loaded it is silently ignored and an empty list
        is returned.  After a successful load the variants from the new file
        are appended to :attr:`variants`.

        Args:
            path: Filesystem path to the ``.odx``, ``.pdx``, or ``.odx-d``
                file to load.  Accepts both :class:`str` and
                :class:`~pathlib.Path`.

        Returns:
            A list of short name strings for every diagnostic layer (ECU
            variant) extracted from the file.  Returns an empty list when the
            file was already loaded.

        Raises:
            Exception: Propagates any error raised by ``odxtools.load_file``
                (e.g. :class:`OSError` for missing files or parse errors for
                malformed ODX content).
        """
        path = Path(path)
        if path in self._files:
            return []
        db = odxtools.load_file(path)
        self._databases.append(db)
        self._files.append(path)
        names = self._extract_variants(db)
        return names

    def _extract_variants(self, db: odxtools.database.Database) -> list[str]:
        """Extract :class:`OdxVariant` objects from an odxtools database.

        Iterates over all diagnostic layers in ``db``, creates an
        :class:`OdxVariant` for each, populates its services and DIDs, and
        appends it to :attr:`_variants`.

        Parse errors for individual services or DIDs are silently suppressed
        so that partially valid files still yield usable data.

        Args:
            db: An odxtools ``Database`` object returned by
                ``odxtools.load_file``.

        Returns:
            A list of short name strings for every variant that was
            successfully extracted.
        """
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
        """Try to extract a DID definition from a single diagnostic service.

        Inspects the constant prefix of the service's request PDU.  If the
        first byte is ``0x22`` (UDS ReadDataByIdentifier) and the prefix is
        at least 3 bytes long, the 2-byte DID is reconstructed and appended
        to ``variant.dids`` (duplicates are skipped).

        All exceptions are silently caught because the odxtools API for
        accessing request prefixes is version-dependent and may not be
        available for all service types.

        Args:
            service: An odxtools ``DiagService`` object whose request PDU is
                inspected.
            variant: The :class:`OdxVariant` to which a new :class:`OdxDid`
                will be appended if a valid DID is found.
        """
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
        """Return all unique DIDs across all loaded variants, sorted by ID.

        De-duplicates by ``did_id``; when the same DID appears in multiple
        variants only the first occurrence (in load order) is included.

        Returns:
            A list of :class:`OdxDid` objects sorted in ascending order by
            :attr:`~OdxDid.did_id`.  Returns an empty list when no files are
            loaded or none of the loaded files contain DID definitions.
        """
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
        """Unload a previously loaded ODX/PDX file and rebuild the variant list.

        Removes the file and its associated odxtools database from the internal
        collections, then clears :attr:`_variants` and reconstructs it from all
        remaining databases.

        If ``path`` is not currently loaded the call is a no-op.

        Args:
            path: Filesystem path of the file to remove.  Must match the path
                originally passed to :meth:`load_file`.
        """
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
        """Unload all files and reset the manager to an empty state.

        Clears the internal database list, file list, and variant list.
        After this call, :attr:`files` and :attr:`variants` both return empty
        collections until new files are loaded.
        """
        self._databases.clear()
        self._files.clear()
        self._variants.clear()
