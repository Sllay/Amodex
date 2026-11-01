"""Explicit, zero-cost-gated multimodal chat capabilities.

Step 3 adds user-triggered media/search actions without changing Stage 5 model
lifecycle or enabling automatic chat inference. Model intelligence remains Step 4.
"""

from amodex.multimodal.clients import (
    MultimodalClients,
    multimodal_clients_from_environment,
)
from amodex.multimodal.models import (
    MessageAttachmentRecord,
    MultimodalOperationKind,
    MultimodalOperationRecord,
    MultimodalOperationStatus,
    ResearchRequest,
    ResearchSource,
    SpeechRequest,
    TranscriptionRequest,
    VisionRequest,
)
from amodex.multimodal.object_store import (
    FileMediaStore,
    MediaObjectStore,
    MemoryMediaStore,
    media_store_from_environment,
)

__all__ = [
    "FileMediaStore",
    "MediaObjectStore",
    "MemoryMediaStore",
    "MessageAttachmentRecord",
    "MultimodalClients",
    "MultimodalOperationKind",
    "MultimodalOperationRecord",
    "MultimodalOperationStatus",
    "ResearchRequest",
    "ResearchSource",
    "SpeechRequest",
    "TranscriptionRequest",
    "VisionRequest",
    "media_store_from_environment",
    "multimodal_clients_from_environment",
]
