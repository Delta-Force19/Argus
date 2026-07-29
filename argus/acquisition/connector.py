from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from argus.acquisition.contracts import (
    CandidateRecord,
    DiscoveryRequest,
    RetrievalResult,
)


@runtime_checkable
class Connector(Protocol):
    """Boundary implemented by protocol-specific acquisition adapters."""

    @property
    def connector_id(self) -> str:
        """Return the stable connector identifier."""

        ...

    @property
    def connector_version(self) -> str:
        """Return the version of connector behavior."""

        ...

    def discover(
            self,
            request: DiscoveryRequest,
    ) -> Iterable[CandidateRecord]:
        """Discover candidates matching normalized constraints."""

        ...

    def retrieve(
            self,
            candidate: CandidateRecord,
    ) -> RetrievalResult:
        """Retrieve one previously discovered candidate."""

        ...
