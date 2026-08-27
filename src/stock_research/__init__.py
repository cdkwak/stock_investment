"""Provider-free, non-executable stock research boundaries."""

from .candidate_discovery import (
    STOCK_CANDIDATE_CONTRACT_VERSION,
    CandidateAxisEvidence,
    StockCandidateDiscoveryView,
    StockCandidateEvidence,
    StockResearchCandidate,
    build_unavailable_candidate_view,
    discover_stock_research_candidates,
    validate_candidate_discovery_view,
)
from .exploratory_scanner import (
    EXPLORATORY_SCANNER_VERSION,
    ExploratoryCandidateView,
    ExploratoryStockCandidate,
    LocalExploratoryCandidateScanner,
    validate_exploratory_candidate_view,
)

__all__ = [
    "STOCK_CANDIDATE_CONTRACT_VERSION",
    "CandidateAxisEvidence",
    "StockCandidateDiscoveryView",
    "StockCandidateEvidence",
    "StockResearchCandidate",
    "build_unavailable_candidate_view",
    "discover_stock_research_candidates",
    "validate_candidate_discovery_view",
    "EXPLORATORY_SCANNER_VERSION",
    "ExploratoryCandidateView",
    "ExploratoryStockCandidate",
    "LocalExploratoryCandidateScanner",
    "validate_exploratory_candidate_view",
]
