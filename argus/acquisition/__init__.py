"""Protocol-independent acquisition contracts."""

from argus.acquisition.artifacts import (
    ArtifactIntegrityError,
    RawArtifactStore,
    StoredArtifact,
)
from argus.acquisition.connector import Connector
from argus.acquisition.contracts import (
    AcquisitionMode,
    CandidateRecord,
    DiscoveryRequest,
    RetrievalOutcome,
    RetrievalResult,
)

__all__ = [
    "AcquisitionMode",
    "ArtifactIntegrityError",
    "CandidateRecord",
    "Connector",
    "DiscoveryRequest",
    "RawArtifactStore",
    "RetrievalOutcome",
    "RetrievalResult",
    "StoredArtifact",
]
