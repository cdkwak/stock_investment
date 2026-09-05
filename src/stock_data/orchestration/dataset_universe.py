from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from stock_data.contracts.registry import CONTRACTS as REGISTERED_CONTRACTS
from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)
from stock_data.contracts.global_etf import GLOBAL_ETF_DAILY_SYMBOLS
from stock_data.contracts.global_equity import GLOBAL_EQUITY_DAILY_SYMBOLS
from stock_data.contracts.kr_fundamentals import KR_FUNDAMENTALS_CONTRACTS
from stock_data.contracts.research_target_prices import (
    RESEARCH_TARGET_PRICE_CONSENSUS,
)
from stock_data.orchestration.tossinvest_us_quotes import TOSSINVEST_US_QUOTE_SYMBOLS


CONTRACTS = {
    **REGISTERED_CONTRACTS,
    KR_INDEX_FUNDAMENTAL_DAILY.name: KR_INDEX_FUNDAMENTAL_DAILY,
    **{contract.name: contract for contract in KR_FUNDAMENTALS_CONTRACTS},
    RESEARCH_TARGET_PRICE_CONSENSUS.name: RESEARCH_TARGET_PRICE_CONSENSUS,
}

# Canonical provider-symbol membership for registered global price datasets.
# Coverage remains dataset-level below; newly registered symbols are not treated
# as retained until a validated Landing-first promotion actually writes rows.
DATASET_SYMBOL_REGISTRY: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "global_index_price_daily": (
        "SP500", "NASDAQ_COMPOSITE", "NASDAQ100", "SOX", "DOW_JONES",
        "DOLLAR_INDEX", "VIX9D", "VIX3M", "VIX6M", "SKEW",
    ),
    "global_etf_price_daily": GLOBAL_ETF_DAILY_SYMBOLS,
    "global_equity_price_daily": GLOBAL_EQUITY_DAILY_SYMBOLS,
    "tossinvest_us_quote_30m": TOSSINVEST_US_QUOTE_SYMBOLS,
    "global_commodity_futures_daily": (
        "NASDAQ100_FUTURES", "GOLD", "WTI_CRUDE_OIL",
        "SP500_FUTURES", "DOW_FUTURES",
    ),
})


class DatasetRefreshClass(StrEnum):
    """Deprecated single-axis compatibility view; never drive new logic."""
    DAILY_SOURCE = "DAILY_SOURCE"
    DERIVED_DEPENDENCY = "DERIVED_DEPENDENCY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    SNAPSHOT = "SNAPSHOT"
    HISTORICAL_STATIC = "HISTORICAL_STATIC"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    BLOCKED = "BLOCKED"


class RegistryDisposition(StrEnum):
    REGISTERED = "REGISTERED"
    INTENTIONALLY_EXCLUDED = "INTENTIONALLY_EXCLUDED"
    REGISTRY_MISSING = "REGISTRY_MISSING"


class SchedulerGroup(StrEnum):
    """Deprecated scheduler compatibility view; use SchedulerManagement."""
    DAILY_API_COLLECTION_REQUIRED = "DAILY_API/COLLECTION_REQUIRED"
    DAILY_DERIVED_REFRESH = "DAILY_DERIVED_REFRESH"
    WEEKLY = "WEEKLY"
    MONTHLY_RELEASE_DRIVEN = "MONTHLY/RELEASE_DRIVEN"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    NO_REFRESH_REQUIRED = "NO_REFRESH_REQUIRED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    BLOCKED = "BLOCKED"


class DataRole(StrEnum):
    SOURCE = "SOURCE"
    SOURCE_OBSERVATION = "SOURCE_OBSERVATION"
    RAW_OBSERVATION = "RAW_OBSERVATION"
    DERIVED = "DERIVED"
    PUBLISHED_BRIDGE = "PUBLISHED_BRIDGE"
    SNAPSHOT = "SNAPSHOT"
    HISTORICAL_SEGMENT = "HISTORICAL_SEGMENT"


class DataGrain(StrEnum):
    INTRADAY = "INTRADAY"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    SNAPSHOT = "SNAPSHOT"
    ON_DEMAND = "ON_DEMAND"
    NONE = "NONE"


class RefreshPolicy(StrEnum):
    GAP_FILL = "GAP_FILL"
    APPEND_EVENT = "APPEND_EVENT"
    UPSTREAM_DEPENDENCY = "UPSTREAM_DEPENDENCY"
    SNAPSHOT_CAPTURE = "SNAPSHOT_CAPTURE"
    STATIC_COMPLETE = "STATIC_COMPLETE"
    MANUAL_RESEARCH = "MANUAL_RESEARCH"
    DISABLED_PENDING_CONTRACT = "DISABLED_PENDING_CONTRACT"


class UniverseOperationalStatus(StrEnum):
    READY = "READY"
    READY_WITH_FINALITY_GATE = "READY_WITH_FINALITY_GATE"
    READY_WITH_LIMITS = "READY_WITH_LIMITS"
    MANUAL_ONLY = "MANUAL_ONLY"
    IMPLEMENTATION_READY = "IMPLEMENTATION_READY"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OperationalBlockerReason(StrEnum):
    SOURCE_CONTRACT = "SOURCE_CONTRACT"
    FINALITY = "FINALITY"
    PERMISSION = "PERMISSION"
    IMPLEMENTATION = "IMPLEMENTATION"
    ACL = "ACL"
    SEMANTICS = "SEMANTICS"
    PIT_ONLY = "PIT_ONLY"
    INTENTIONAL = "INTENTIONAL"


class PredictivePitStatus(StrEnum):
    PIT_SAFE = "PIT_SAFE"
    PIT_LIMITED = "PIT_LIMITED"
    PIT_BLOCKED = "PIT_BLOCKED"
    NON_PREDICTIVE = "NON_PREDICTIVE"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class AutomationPolicy(StrEnum):
    AUTO_ELIGIBLE = "AUTO_ELIGIBLE"
    MANUAL_GATE = "MANUAL_GATE"
    DEPENDENCY_DRIVEN = "DEPENDENCY_DRIVEN"
    NO_REFRESH = "NO_REFRESH"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"


class GuiUse(StrEnum):
    DIRECT = "DIRECT"
    STATUS_ONLY = "STATUS_ONLY"
    DESCRIPTIVE = "DESCRIPTIVE"
    NONE = "NONE"


class ConsumerEligibility(StrEnum):
    """Independent permission for one bounded class of data consumer."""

    ELIGIBLE = "ELIGIBLE"
    LIMITED = "LIMITED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class ConsumerReasonCode(StrEnum):
    """Bounded evidence codes; free text must not decide consumer permission."""

    DISPLAY_DIRECT_CONTRACT = "DISPLAY_DIRECT_CONTRACT"
    DISPLAY_DESCRIPTIVE_CONTRACT = "DISPLAY_DESCRIPTIVE_CONTRACT"
    DISPLAY_STATUS_ONLY = "DISPLAY_STATUS_ONLY"
    DISPLAY_NOT_CONTRACTED = "DISPLAY_NOT_CONTRACTED"
    RESEARCH_RETAINED_CONTRACT = "RESEARCH_RETAINED_CONTRACT"
    RESEARCH_RETAINED_EVIDENCE_ONLY = "RESEARCH_RETAINED_EVIDENCE_ONLY"
    RESEARCH_CONTRACT_NO_RETAINED_EVIDENCE = "RESEARCH_CONTRACT_NO_RETAINED_EVIDENCE"
    RESEARCH_NOT_CONTRACTED = "RESEARCH_NOT_CONTRACTED"
    PREDICTIVE_PIT_SAFE = "PREDICTIVE_PIT_SAFE"
    PREDICTIVE_PIT_LIMITED = "PREDICTIVE_PIT_LIMITED"
    PREDICTIVE_PIT_BLOCKED = "PREDICTIVE_PIT_BLOCKED"
    PREDICTIVE_NON_PREDICTIVE = "PREDICTIVE_NON_PREDICTIVE"
    PREDICTIVE_RESEARCH_ONLY = "PREDICTIVE_RESEARCH_ONLY"
    NOT_CLASSIFIED = "NOT_CLASSIFIED"


_CONSUMER_REASON_ELIGIBILITY: Mapping[ConsumerReasonCode, ConsumerEligibility] = (
    MappingProxyType({
        ConsumerReasonCode.DISPLAY_DIRECT_CONTRACT: ConsumerEligibility.ELIGIBLE,
        ConsumerReasonCode.DISPLAY_DESCRIPTIVE_CONTRACT: ConsumerEligibility.LIMITED,
        ConsumerReasonCode.DISPLAY_STATUS_ONLY: ConsumerEligibility.LIMITED,
        ConsumerReasonCode.DISPLAY_NOT_CONTRACTED: ConsumerEligibility.BLOCKED,
        ConsumerReasonCode.RESEARCH_RETAINED_CONTRACT: ConsumerEligibility.ELIGIBLE,
        ConsumerReasonCode.RESEARCH_RETAINED_EVIDENCE_ONLY: ConsumerEligibility.LIMITED,
        ConsumerReasonCode.RESEARCH_CONTRACT_NO_RETAINED_EVIDENCE: ConsumerEligibility.LIMITED,
        ConsumerReasonCode.RESEARCH_NOT_CONTRACTED: ConsumerEligibility.BLOCKED,
        ConsumerReasonCode.PREDICTIVE_PIT_SAFE: ConsumerEligibility.ELIGIBLE,
        ConsumerReasonCode.PREDICTIVE_PIT_LIMITED: ConsumerEligibility.BLOCKED,
        ConsumerReasonCode.PREDICTIVE_PIT_BLOCKED: ConsumerEligibility.BLOCKED,
        ConsumerReasonCode.PREDICTIVE_NON_PREDICTIVE: ConsumerEligibility.BLOCKED,
        ConsumerReasonCode.PREDICTIVE_RESEARCH_ONLY: ConsumerEligibility.BLOCKED,
        ConsumerReasonCode.NOT_CLASSIFIED: ConsumerEligibility.UNKNOWN,
    })
)


def validate_consumer_decision(
    axis: str,
    eligibility: ConsumerEligibility,
    reason_code: ConsumerReasonCode,
) -> None:
    """Reject cross-axis reasons and eligibility/reason contradictions."""

    if axis not in {"display", "research", "predictive"}:
        raise ValueError("consumer axis is invalid")
    if not isinstance(eligibility, ConsumerEligibility):
        raise TypeError(f"{axis}_consumer_eligibility must be ConsumerEligibility")
    if not isinstance(reason_code, ConsumerReasonCode):
        raise TypeError(f"{axis}_consumer_reason must be ConsumerReasonCode")
    if reason_code is not ConsumerReasonCode.NOT_CLASSIFIED and not reason_code.value.startswith(
        f"{axis.upper()}_"
    ):
        raise ValueError(f"{axis} consumer reason belongs to another axis")
    if _CONSUMER_REASON_ELIGIBILITY[reason_code] is not eligibility:
        raise ValueError(f"{axis} consumer eligibility contradicts its reason")


def _display_decision(gui_use: GuiUse) -> tuple[ConsumerEligibility, ConsumerReasonCode]:
    return {
        GuiUse.DIRECT: (
            ConsumerEligibility.ELIGIBLE,
            ConsumerReasonCode.DISPLAY_DIRECT_CONTRACT,
        ),
        GuiUse.DESCRIPTIVE: (
            ConsumerEligibility.LIMITED,
            ConsumerReasonCode.DISPLAY_DESCRIPTIVE_CONTRACT,
        ),
        GuiUse.STATUS_ONLY: (
            ConsumerEligibility.LIMITED,
            ConsumerReasonCode.DISPLAY_STATUS_ONLY,
        ),
        GuiUse.NONE: (
            ConsumerEligibility.BLOCKED,
            ConsumerReasonCode.DISPLAY_NOT_CONTRACTED,
        ),
    }[gui_use]


def _research_decision(
    *, retained: bool, contract_version: int | None,
) -> tuple[ConsumerEligibility, ConsumerReasonCode]:
    if retained and contract_version is not None:
        return (
            ConsumerEligibility.ELIGIBLE,
            ConsumerReasonCode.RESEARCH_RETAINED_CONTRACT,
        )
    if retained:
        return (
            ConsumerEligibility.LIMITED,
            ConsumerReasonCode.RESEARCH_RETAINED_EVIDENCE_ONLY,
        )
    if contract_version is not None:
        return (
            ConsumerEligibility.LIMITED,
            ConsumerReasonCode.RESEARCH_CONTRACT_NO_RETAINED_EVIDENCE,
        )
    return ConsumerEligibility.BLOCKED, ConsumerReasonCode.RESEARCH_NOT_CONTRACTED


def _predictive_decision(
    pit_status: PredictivePitStatus,
) -> tuple[ConsumerEligibility, ConsumerReasonCode]:
    reason = {
        PredictivePitStatus.PIT_SAFE: ConsumerReasonCode.PREDICTIVE_PIT_SAFE,
        PredictivePitStatus.PIT_LIMITED: ConsumerReasonCode.PREDICTIVE_PIT_LIMITED,
        PredictivePitStatus.PIT_BLOCKED: ConsumerReasonCode.PREDICTIVE_PIT_BLOCKED,
        PredictivePitStatus.NON_PREDICTIVE: ConsumerReasonCode.PREDICTIVE_NON_PREDICTIVE,
        PredictivePitStatus.RESEARCH_ONLY: ConsumerReasonCode.PREDICTIVE_RESEARCH_ONLY,
    }[pit_status]
    eligibility = (
        ConsumerEligibility.ELIGIBLE
        if pit_status is PredictivePitStatus.PIT_SAFE
        else ConsumerEligibility.BLOCKED
    )
    return eligibility, reason


class SchedulerManagement(StrEnum):
    DIRECT_COLLECTION_MANAGED = "DIRECT_COLLECTION_MANAGED"
    DEPENDENCY_REFRESH_MANAGED = "DEPENDENCY_REFRESH_MANAGED"
    WEEKLY_MANAGED = "WEEKLY_MANAGED"
    EVENT_MANAGED = "EVENT_MANAGED"
    MANUAL_ONLY = "MANUAL_ONLY"
    NO_REFRESH = "NO_REFRESH"
    RESEARCH = "RESEARCH"
    BLOCKED = "BLOCKED"


class HealthDisplayStatus(StrEnum):
    """User-facing operational freshness, separate from provider lag semantics."""

    CURRENT = "CURRENT"
    LATE = "LATE"
    FAILED = "FAILED"
    PRESERVED = "PRESERVED"
    REFERENCE = "REFERENCE"


@dataclass(frozen=True)
class DatasetUniverseSpec:
    dataset_id: str
    economic_variable: str
    layer: str
    source: str
    coverage_start: str | None
    retained_latest: str | None
    contract_version: int | None
    data_role: DataRole
    data_grain: DataGrain
    refresh_policy: RefreshPolicy
    operational_status: UniverseOperationalStatus
    operational_blocker_reason: OperationalBlockerReason | None
    predictive_pit_status: PredictivePitStatus
    automation_policy: AutomationPolicy
    automation_enabled: bool
    scheduler_lane: str
    gui_use: GuiUse
    display_consumer_eligibility: ConsumerEligibility
    display_consumer_reason: ConsumerReasonCode
    research_consumer_eligibility: ConsumerEligibility
    research_consumer_reason: ConsumerReasonCode
    predictive_consumer_eligibility: ConsumerEligibility
    predictive_consumer_reason: ConsumerReasonCode
    scheduler_management: SchedulerManagement
    health_preservation_reason: str | None
    # Deprecated compatibility fields. New logic must use the orthogonal axes above.
    primary_classification: DatasetRefreshClass
    secondary_roles: tuple[DatasetRefreshClass, ...]
    operations_registry_present_before: bool
    operations_registry_entry: str | None
    registry_present: bool
    registry_entry: str
    cadence: str
    lane: str
    upstream_dependencies: tuple[str, ...]
    downstream_dependencies: tuple[str, ...]
    automation_required: bool
    prior_disposition: RegistryDisposition
    reason_if_not_registered_before: str | None
    scheduler_group: SchedulerGroup
    retained: bool
    physical_artifacts: tuple[str, ...]

    def __post_init__(self) -> None:
        typed_axes = {
            "data_role": (self.data_role, DataRole),
            "data_grain": (self.data_grain, DataGrain),
            "refresh_policy": (self.refresh_policy, RefreshPolicy),
            "operational_status": (self.operational_status, UniverseOperationalStatus),
            "predictive_pit_status": (self.predictive_pit_status, PredictivePitStatus),
            "automation_policy": (self.automation_policy, AutomationPolicy),
            "gui_use": (self.gui_use, GuiUse),
            "display_consumer_eligibility": (
                self.display_consumer_eligibility, ConsumerEligibility,
            ),
            "display_consumer_reason": (self.display_consumer_reason, ConsumerReasonCode),
            "research_consumer_eligibility": (
                self.research_consumer_eligibility, ConsumerEligibility,
            ),
            "research_consumer_reason": (self.research_consumer_reason, ConsumerReasonCode),
            "predictive_consumer_eligibility": (
                self.predictive_consumer_eligibility, ConsumerEligibility,
            ),
            "predictive_consumer_reason": (
                self.predictive_consumer_reason, ConsumerReasonCode,
            ),
            "scheduler_management": (self.scheduler_management, SchedulerManagement),
        }
        for name, (value, expected_type) in typed_axes.items():
            if not isinstance(value, expected_type):
                raise TypeError(f"{name} must be {expected_type.__name__}")
        if self.operational_blocker_reason is not None and not isinstance(
            self.operational_blocker_reason, OperationalBlockerReason
        ):
            raise TypeError("operational_blocker_reason must be OperationalBlockerReason or None")
        if self.operational_status is UniverseOperationalStatus.BLOCKED:
            if self.operational_blocker_reason is None:
                raise ValueError("BLOCKED datasets require operational_blocker_reason")
        elif self.operational_blocker_reason is not None:
            raise ValueError("only BLOCKED datasets may carry operational_blocker_reason")
        if self.health_preservation_reason is not None and not self.health_preservation_reason.strip():
            raise ValueError("health_preservation_reason must be non-empty text or None")
        if self.data_role in {DataRole.DERIVED, DataRole.PUBLISHED_BRIDGE}:
            if self.refresh_policy is not RefreshPolicy.UPSTREAM_DEPENDENCY:
                raise ValueError("derived/bridge datasets must refresh from upstream dependencies")
            if self.automation_policy is not AutomationPolicy.DEPENDENCY_DRIVEN:
                raise ValueError("derived/bridge datasets require DEPENDENCY_DRIVEN automation")
        if self.data_role is DataRole.HISTORICAL_SEGMENT:
            expected = (
                RefreshPolicy.STATIC_COMPLETE,
                UniverseOperationalStatus.NOT_APPLICABLE,
                AutomationPolicy.NO_REFRESH,
            )
            actual = (self.refresh_policy, self.operational_status, self.automation_policy)
            if actual != expected:
                raise ValueError("historical segments must be static, not applicable, and no-refresh")
        if self.automation_enabled and (
            self.automation_policy not in {
                AutomationPolicy.AUTO_ELIGIBLE, AutomationPolicy.DEPENDENCY_DRIVEN,
            }
            or self.operational_status not in {
                UniverseOperationalStatus.READY,
                UniverseOperationalStatus.READY_WITH_FINALITY_GATE,
                UniverseOperationalStatus.READY_WITH_LIMITS,
            }
        ):
            raise ValueError(
                "enabled automation requires eligible direct/dependency policy and ready status"
            )
        expected_decisions = (
            _display_decision(self.gui_use),
            _research_decision(
                retained=self.retained, contract_version=self.contract_version,
            ),
            _predictive_decision(self.predictive_pit_status),
        )
        actual_decisions = (
            (self.display_consumer_eligibility, self.display_consumer_reason),
            (self.research_consumer_eligibility, self.research_consumer_reason),
            (self.predictive_consumer_eligibility, self.predictive_consumer_reason),
        )
        for axis, actual, expected in zip(
            ("display", "research", "predictive"),
            actual_decisions,
            expected_decisions,
            strict=True,
        ):
            validate_consumer_decision(axis, *actual)
            if actual != expected:
                raise ValueError(f"{axis} consumer decision lacks matching evidence")


class DatasetUniverseRegistry(Mapping[str, DatasetUniverseSpec]):
    def __init__(self, specs: Iterable[DatasetUniverseSpec]) -> None:
        items = tuple(specs)
        ids = tuple(item.dataset_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate dataset universe id")
        self._specs = MappingProxyType(
            {item.dataset_id: item for item in sorted(items, key=lambda item: item.dataset_id)}
        )

    def __getitem__(self, dataset_id: str) -> DatasetUniverseSpec:
        return self._specs[dataset_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)


_CLASSIFICATION_IDS = {
    DatasetRefreshClass.DAILY_SOURCE: frozenset("""
        kr_index_daily kr_index_fundamental_daily kr_equity_price_daily
        kr_equity_price_provisional_daily kr_equity_investor_flow_daily
        kr_equity_market_cap_daily kr_equity_universe_daily
        kr_etf_master kr_etf_price_daily
        global_index_price_daily global_etf_price_daily global_equity_price_daily
        global_commodity_futures_daily tossinvest_us_quote_30m cboe_daily_pcr_daily
        kbsec_transactions_daily
        fred_treasury_yield_daily fred_usd_fx_daily fred_vix_daily
        bok_ecos_usd_krw_daily
        kr_short_selling_balance_daily kr_short_selling_investor_daily kr_market_liquidity_daily
        kr_credit_balance_daily kr_stock_lending_daily kr_stock_lending_market_daily
        kr_stock_lending_participant_daily kr_kospi200_index_daily kr_vkospi_daily
        kr_kospi200_futures_daily kr_kospi200_options_daily
        kr_market_investor_trading_daily kr_treasury_yield_daily
        bok_ecos_kr_treasury_yield_source_observation
        kr_kospi200_futures_investor_net_purchase_daily
        kr_index_constituent_daily
        market_price_60m_observation market_price_15m_observation
    """.split()),
    DatasetRefreshClass.DERIVED_DEPENDENCY: frozenset("""
        kr_equity_canonical_universe_daily kr_market_breadth_daily us_treasury_spread_daily
        us_vix_term_structure_daily
        kr_market_investor_net_purchase_bridge_daily kr_kospi200_futures_provider_bridge_daily
        kr_kospi200_options_provider_bridge_daily kr_kospi200_futures_nearest_listed_daily
        kr_kospi200_option_pcr_daily kr_kospi200_option_walls_daily
        kr_kospi200_constituent_price_daily kr_kospi200_breadth_daily
    """.split()),
    DatasetRefreshClass.WEEKLY: frozenset("""
        kr_corp_code_map
        us_cftc_legacy_futures_only_raw us_cftc_legacy_futures_options_combined_raw
        us_cftc_tff_futures_only_raw us_cftc_disaggregated_futures_only_raw
    """.split()),
    DatasetRefreshClass.MONTHLY: frozenset(),
    DatasetRefreshClass.EVENT_DRIVEN: frozenset("""
        kr_fundamentals_quarterly
        kr_equity_master kr_equity_dividend kr_equity_rights_schedule
        kr_equity_dividend_source_observation kr_equity_stock_issuance_source_observation
    """.split()),
    DatasetRefreshClass.SNAPSHOT: frozenset("""
        kb_market_breadth_snapshot kb_program_trading_snapshot kb_investor_flow_snapshot
        kb_market_liquidity_snapshot kb_derivatives_summary_snapshot kb_domestic_index_snapshot
        kb_global_symbol_snapshot
    """.split()),
    DatasetRefreshClass.HISTORICAL_STATIC: frozenset("""
        krx_legacy_kospi200_futures_daily krx_legacy_kospi200_options_daily
        kr_market_investor_net_purchase_daily kr_equity_foreign_ownership_daily
        kr_equity_fundamental_daily kr_etf_universe_daily kr_etf_ohlcv_daily
        kr_credit_benchmark_yield_daily
    """.split()),
    DatasetRefreshClass.RESEARCH_ONLY: frozenset(
        {
            "ls_t1633_program_trading_candidate",
            "ls_t8462_daily_raw",
            "research_target_price_consensus",
        }
    ),
    DatasetRefreshClass.BLOCKED: frozenset("""
        kr_derivatives_futures_daily kr_derivatives_options_daily
        kr_kosdaq150_futures_daily kr_kosdaq150_options_daily kr_equity_short_selling_daily
        kr_equity_program_trading_daily kr_equity_securities_lending_daily
        kr_equity_credit_trading_daily kr_investor_flow_daily
        kr_kospi200_futures_investor_trading_daily
        kr_kospi200_options_investor_trading_daily
        ls_t8428_surrounding_funds_source_observation kr_short_selling_trading_daily
        kr_equity_sector_classification
    """.split()),
}


_NON_CONTRACT = {
    "us_cftc_legacy_futures_only_raw": ("CFTC legacy futures-only positions", "landing", "CFTC historical compressed archives"),
    "us_cftc_legacy_futures_options_combined_raw": ("CFTC legacy futures-and-options-combined positions", "landing", "CFTC historical compressed archives"),
    "us_cftc_tff_futures_only_raw": ("CFTC Traders in Financial Futures positions", "landing", "CFTC historical compressed archives"),
    "us_cftc_disaggregated_futures_only_raw": ("CFTC disaggregated futures positions", "landing", "CFTC historical compressed archives"),
    "kr_equity_foreign_ownership_daily": ("Korean equity foreign ownership", "landing", "KRX/pykrx MDCSTAT03701"),
    "kr_equity_fundamental_daily": ("Korean equity valuation fundamentals", "landing", "KRX/pykrx MDCSTAT03501"),
    "kr_etf_universe_daily": ("Korean ETF point-in-time universe", "landing", "KRX/pykrx MDCSTAT04301"),
    "kr_etf_ohlcv_daily": ("Korean ETF daily OHLCV", "landing", "KRX/pykrx MDCSTAT04301 shared bytes"),
    "kr_credit_benchmark_yield_daily": ("Korean credit benchmark yields", "landing", "KRX/pykrx"),
    "kr_equity_sector_classification": ("Korean equity sector classification", "landing", "KRX/pykrx bounded pilot"),
    "ls_t1633_program_trading_candidate": ("Korean market program trading", "landing", "LS OpenAPI t1633"),
    "ls_t8462_daily_raw": ("KOSPI200 derivatives investor flow", "landing", "LS OpenAPI t8462"),
}


_COVERAGE = {
    "market_price_60m_observation": ("2026-08-12", "2026-08-19"),
    "market_price_15m_observation": ("2026-08-19", "2026-08-21"),
    "kr_index_daily": ("1975-01-04", "2026-08-19"),
    "kr_equity_price_daily": ("1995-05-02", "2026-08-13"),
    "kr_equity_market_cap_daily": ("1995-05-02", "2026-08-13"),
    "kr_equity_master": ("2026-08-08", "2026-08-08"),
    "kr_equity_universe_daily": ("1995-05-02", "2026-08-13"),
    "kr_equity_canonical_universe_daily": ("1995-05-02", "2026-08-13"),
    "kr_market_breadth_daily": ("1995-05-02", "2026-08-13"),
    "kr_kospi200_constituent_price_daily": ("2026-08-12", "2026-08-25"),
    "kr_kospi200_breadth_daily": ("2026-08-12", "2026-08-25"),
    "global_index_price_daily": ("1928-01-03", "2026-08-18"),
    "global_etf_price_daily": ("2025-08-18", "2026-08-18"),
    "global_equity_price_daily": ("2026-07-13", "2026-09-03"),
    "tossinvest_us_quote_30m": ("2026-09-04", "2026-09-04"),
    "global_commodity_futures_daily": ("2025-08-18", "2026-08-18"),
    "fred_treasury_yield_daily": ("1962-01-02", "2026-08-17"),
    "fred_usd_fx_daily": ("1971-01-04", "2026-08-14"),
    "fred_vix_daily": ("1990-01-02", "2026-08-17"),
    "us_treasury_spread_daily": ("1962-01-02", "2026-08-17"),
    "kr_short_selling_trading_daily": ("2008-01-02", "2026-08-19"),
    "kr_short_selling_balance_daily": ("2016-06-30", "2026-08-13"),
    "kr_short_selling_investor_daily": ("2017-05-22", "2026-08-14"),
    "kr_market_liquidity_daily": ("2021-10-26", "2026-08-06"),
    "kr_credit_balance_daily": ("2021-11-09", "2026-08-06"),
    "kr_derivatives_futures_daily": ("2022-09-19", "2022-09-19"),
    "kr_derivatives_options_daily": ("2022-09-19", "2022-09-19"),
    "kr_stock_lending_daily": ("2021-04-01", "2026-08-14"),
    "kr_stock_lending_market_daily": ("2021-04-01", "2026-08-14"),
    "kr_stock_lending_participant_daily": ("2021-04-01", "2026-08-14"),
    "kr_equity_dividend": ("2026-08-08", "2026-08-08"),
    "kr_equity_rights_schedule": ("2019-12-31", "2019-12-31"),
    "kr_kospi200_futures_daily": ("2020-01-02", "2026-08-25"),
    "kr_kospi200_options_daily": ("2020-01-02", "2026-08-25"),
    "kr_kosdaq150_futures_daily": ("2022-09-19", "2022-09-19"),
    "kr_kosdaq150_options_daily": ("2022-09-19", "2022-09-19"),
    "kb_market_breadth_snapshot": ("2026-08-17", "2026-08-18"),
    "kb_program_trading_snapshot": ("2026-08-17", "2026-08-17"),
    "kb_investor_flow_snapshot": ("2026-08-17", "2026-08-17"),
    "kb_market_liquidity_snapshot": ("2026-08-17", "2026-08-17"),
    "kb_derivatives_summary_snapshot": ("2026-08-17", "2026-08-17"),
    "kb_domestic_index_snapshot": ("2026-08-17", "2026-08-17"),
    "kb_global_symbol_snapshot": ("2026-08-17", "2026-08-17"),
    "krx_legacy_kospi200_futures_daily": ("2010-01-04", "2019-12-30"),
    "krx_legacy_kospi200_options_daily": ("2010-01-04", "2019-12-30"),
    "kr_kospi200_option_pcr_daily": ("2010-01-04", "2026-08-25"),
    "kr_market_investor_net_purchase_daily": ("1999-01-04", "2014-06-30"),
    "kr_market_investor_net_purchase_bridge_daily": ("1999-01-04", "2026-08-19"),
    "kr_equity_dividend_source_observation": ("2026-08-08", "2026-08-08"),
    "kr_kospi200_futures_provider_bridge_daily": ("2010-01-04", "2026-08-25"),
    "kr_kospi200_options_provider_bridge_daily": ("2010-01-04", "2026-08-25"),
    "kr_kospi200_futures_nearest_listed_daily": ("2010-01-04", "2026-08-25"),
    "kr_kospi200_option_walls_daily": ("2010-01-04", "2026-08-25"),
    "kr_kospi200_index_daily": ("1990-01-03", "2026-08-19"),
    "kr_vkospi_daily": ("2003-01-02", "2026-08-19"),
    "kr_market_investor_trading_daily": ("2014-07-01", "2026-08-19"),
    "kr_treasury_yield_daily": ("2019-01-02", "2026-08-10"),
    "bok_ecos_kr_treasury_yield_source_observation": ("1998-11-13", "2026-08-13"),
    "kr_equity_stock_issuance_source_observation": ("2020-07-14", "2026-08-12"),
    "kr_kospi200_futures_investor_net_purchase_daily": ("1999-04-26", "2026-08-13"),
    "us_cftc_legacy_futures_only_raw": ("1986", "2026-08-11"),
    "us_cftc_legacy_futures_options_combined_raw": ("1995", "2026-08-11"),
    "us_cftc_tff_futures_only_raw": ("2006-06-13", "2026-08-11"),
    "us_cftc_disaggregated_futures_only_raw": ("2006-06-13", "2026-08-11"),
    "kr_equity_foreign_ownership_daily": ("2000-01-05", "2026-08-12"),
    "kr_equity_fundamental_daily": ("2008-01-03", "2026-08-12"),
    "kr_etf_universe_daily": ("2008-01-02", "2026-08-12"),
    "kr_etf_ohlcv_daily": ("2008-01-02", "2026-08-12"),
    "kr_index_fundamental_daily": ("2000-01-04", "2026-08-25"),
    "kr_credit_benchmark_yield_daily": ("2002", "2026-08-12"),
    "kr_index_constituent_daily": ("2026-08-12", "2026-08-25"),
    "kr_equity_sector_classification": ("2020-01-02", "2026-08-14"),
    "ls_t8428_surrounding_funds_source_observation": ("2006", "2026-08-14"),
    "ls_t1633_program_trading_candidate": ("2001-08-01", "2026-08-14"),
    "ls_t8462_daily_raw": ("2025-07-18", "2026-08-14"),
}


_UPSTREAM = {
    "kr_equity_canonical_universe_daily": ("kr_equity_price_daily", "kr_equity_market_cap_daily", "kr_equity_universe_daily", "kr_equity_master"),
    "kr_market_breadth_daily": ("kr_equity_canonical_universe_daily", "kr_equity_price_daily"),
    "kr_kospi200_constituent_price_daily": ("kr_index_constituent_daily", "kr_equity_price_daily"),
    "kr_kospi200_breadth_daily": ("kr_kospi200_constituent_price_daily", "kr_equity_price_daily"),
    "us_treasury_spread_daily": ("fred_treasury_yield_daily",),
    "us_vix_term_structure_daily": ("fred_vix_daily", "global_index_price_daily"),
    "kr_market_investor_net_purchase_bridge_daily": ("kr_market_investor_net_purchase_daily", "kr_market_investor_trading_daily"),
    "kr_kospi200_futures_provider_bridge_daily": ("krx_legacy_kospi200_futures_daily", "kr_kospi200_futures_daily"),
    "kr_kospi200_options_provider_bridge_daily": ("krx_legacy_kospi200_options_daily", "kr_kospi200_options_daily"),
    "kr_kospi200_futures_nearest_listed_daily": ("kr_kospi200_futures_provider_bridge_daily", "kr_kospi200_index_daily"),
    "kr_kospi200_option_pcr_daily": ("kr_kospi200_options_provider_bridge_daily",),
    "kr_kospi200_option_walls_daily": ("kr_kospi200_options_provider_bridge_daily", "kr_kospi200_index_daily"),
    "kr_kospi200_futures_investor_net_purchase_daily": ("kr_kospi200_futures_investor_trading_daily",),
}


_LANE_GROUPS = {
    "MARKET_15M": frozenset({"market_price_15m_observation"}),
    "CANONICAL_EQUITY_DAILY": frozenset("""
        kr_equity_canonical_universe_daily kr_equity_price_daily
        kr_equity_market_cap_daily kr_equity_universe_daily kr_market_breadth_daily
    """.split()),
    "KR_EQUITY_PROVISIONAL_DAILY": frozenset({
        "kr_equity_price_provisional_daily",
    }),
    "KR_EQUITY_INVESTOR_FLOW_DAILY": frozenset({
        "kr_equity_investor_flow_daily",
    }),
    "KR_INDEX_DAILY": frozenset({"kr_index_daily", "kr_kospi200_index_daily"}),
    "KR_FUNDAMENTALS_WEEKLY": frozenset({
        "kr_corp_code_map", "kr_fundamentals_quarterly",
    }),
    "KR_INDEX_FUNDAMENTAL_DAILY": frozenset({"kr_index_fundamental_daily"}),
    "KOSPI200_BREADTH_DAILY": frozenset({
        "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
    }),
    "GLOBAL_INDEX_DAILY": frozenset({
        "global_index_price_daily", "us_vix_term_structure_daily",
    }),
    "FRED_DAILY": frozenset({"fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily", "us_treasury_spread_daily"}),
    "GLOBAL_ETF_DAILY": frozenset({"global_etf_price_daily"}),
    "GLOBAL_EQUITY_DAILY": frozenset({"global_equity_price_daily"}),
    "TOSSINVEST_US_QUOTES_30M": frozenset({"tossinvest_us_quote_30m"}),
    "CBOE_DAILY_PCR": frozenset({"cboe_daily_pcr_daily"}),
    "KB_TRANSACTIONS_DAILY": frozenset({"kbsec_transactions_daily"}),
    "KR_ETF_PRICE_DAILY": frozenset({"kr_etf_master", "kr_etf_price_daily"}),
    "GLOBAL_COMMODITY_DAILY": frozenset({"global_commodity_futures_daily"}),
    "VKOSPI_DAILY": frozenset({"kr_vkospi_daily"}),
    "LS_T8462_DAILY": frozenset({"ls_t8462_daily_raw"}),
    "DERIVATIVES_PRICE_DAILY": frozenset("""
        kr_kospi200_futures_daily kr_kospi200_options_daily
        kr_kospi200_futures_provider_bridge_daily kr_kospi200_options_provider_bridge_daily
        kr_kospi200_futures_nearest_listed_daily kr_kospi200_option_pcr_daily
        kr_kospi200_option_walls_daily
    """.split()),
    "DERIVATIVES_INVESTOR_DAILY": frozenset("""
        kr_kospi200_futures_investor_trading_daily
        kr_kospi200_options_investor_trading_daily
        kr_kospi200_futures_investor_net_purchase_daily
    """.split()),
    "SHORT_SELLING_DAILY": frozenset("""
        kr_short_selling_trading_daily
    """.split()),
    "SHORT_SELLING_BALANCE_DAILY": frozenset({"kr_short_selling_balance_daily"}),
    "SHORT_SELLING_INVESTOR_DAILY": frozenset({"kr_short_selling_investor_daily"}),
    "LENDING_DAILY": frozenset("""
        kr_stock_lending_daily kr_stock_lending_market_daily
        kr_stock_lending_participant_daily
    """.split()),
    "LIQUIDITY_CREDIT_DAILY": frozenset("""
        kr_market_liquidity_daily kr_credit_balance_daily
    """.split()),
    "MARKET_INVESTOR_DAILY": frozenset("""
        kr_market_investor_trading_daily kr_market_investor_net_purchase_bridge_daily
    """.split()),
    "TOSS_KR_TREASURY_DAILY": frozenset({"kr_treasury_yield_daily"}),
    "BOK_TREASURY_OBSERVATION_DAILY": frozenset({"bok_ecos_kr_treasury_yield_source_observation"}),
    "BOK_FX_DAILY": frozenset({"bok_ecos_usd_krw_daily"}),
    "CORPORATE_ACTION_EVENT": frozenset("""
        kr_equity_master kr_equity_dividend kr_equity_rights_schedule
        kr_equity_dividend_source_observation kr_equity_stock_issuance_source_observation
    """.split()),
    "BROKER_SNAPSHOT": frozenset("""
        kb_market_breadth_snapshot kb_program_trading_snapshot kb_investor_flow_snapshot
        kb_market_liquidity_snapshot kb_derivatives_summary_snapshot kb_domestic_index_snapshot
        kb_global_symbol_snapshot
    """.split()),
}


_ECONOMIC_GROUPS = {
    "watchlist_target_price_consensus": ("research_target_price_consensus",),
    "kr_issuer_fundamentals": ("kr_corp_code_map", "kr_fundamentals_quarterly"),
    "kr_equity_universe": ("kr_equity_universe_daily", "kr_equity_canonical_universe_daily"),
    "kr_equity_index_level": ("kr_index_daily", "kr_kospi200_index_daily", "kb_domestic_index_snapshot"),
    "global_market_price": (
        "global_index_price_daily", "global_equity_price_daily",
        "tossinvest_us_quote_30m", "market_price_15m_observation",
        "kb_global_symbol_snapshot",
    ),
    "cboe_option_sentiment": ("cboe_daily_pcr_daily",),
    "kr_equity_price": (
        "kr_equity_price_daily", "kr_equity_price_provisional_daily",
        "kr_kospi200_constituent_price_daily",
    ),
    "kr_equity_investor_flow": ("kr_equity_investor_flow_daily",),
    "kr_etf_identity_price": ("kr_etf_master", "kr_etf_price_daily"),
    "kr_index_membership": ("kr_index_constituent_daily",),
    "kr_market_breadth": (
        "kr_market_breadth_daily", "kb_market_breadth_snapshot",
        "kr_kospi200_breadth_daily",
    ),
    "kr_market_investor_flow": ("kr_investor_flow_daily", "kr_market_investor_net_purchase_daily", "kr_market_investor_trading_daily", "kr_market_investor_net_purchase_bridge_daily", "kb_investor_flow_snapshot"),
    "kr_market_liquidity": ("kr_market_liquidity_daily", "kb_market_liquidity_snapshot"),
    "kr_credit_balance": ("kr_credit_balance_daily", "kr_equity_credit_trading_daily"),
    "kr_program_trading": ("kr_equity_program_trading_daily", "kb_program_trading_snapshot", "ls_t1633_program_trading_candidate"),
    "kr_short_selling_trading": ("kr_short_selling_trading_daily", "kr_equity_short_selling_daily"),
    "kr_stock_lending_detail": ("kr_stock_lending_daily", "kr_equity_securities_lending_daily"),
    "kr_dividend_event": ("kr_equity_dividend", "kr_equity_dividend_source_observation"),
    "kr_treasury_yield": ("kr_treasury_yield_daily", "bok_ecos_kr_treasury_yield_source_observation"),
    "kospi200_futures_price": ("kr_kospi200_futures_daily", "krx_legacy_kospi200_futures_daily", "kr_kospi200_futures_provider_bridge_daily"),
    "kospi200_options_price": ("kr_kospi200_options_daily", "krx_legacy_kospi200_options_daily", "kr_kospi200_options_provider_bridge_daily"),
    "kospi200_futures_investor_flow": ("kr_kospi200_futures_investor_trading_daily", "kr_kospi200_futures_investor_net_purchase_daily"),
    "us_cftc_positions": ("us_cftc_legacy_futures_only_raw", "us_cftc_legacy_futures_options_combined_raw", "us_cftc_tff_futures_only_raw", "us_cftc_disaggregated_futures_only_raw"),
}


_ECONOMIC_LABELS = {
    "watchlist_target_price_consensus": "Dated watchlist analyst target-price consensus",
    "kr_issuer_fundamentals": "Korean issuer identity and quarterly financial health",
    "kr_equity_universe": "Korean equity point-in-time universe",
    "kr_equity_index_level": "Korean equity index levels and OHLCV",
    "global_market_price": "Global market index/symbol prices",
    "cboe_option_sentiment": "Cboe venue-scoped option product-group put/call ratios",
    "kr_equity_price": "Korean equity daily prices",
    "kr_equity_investor_flow": "Korean per-equity investor net-purchase flow",
    "kr_etf_identity_price": "Korean ETF current identity and daily prices",
    "kr_index_membership": "Korean index exact-date membership",
    "kr_market_breadth": "Korean equity market breadth",
    "kr_market_investor_flow": "Korean market investor net trading flow",
    "kr_market_liquidity": "Korean securities-market liquidity",
    "kr_credit_balance": "Korean securities credit balances",
    "kr_program_trading": "Korean market program trading",
    "kr_short_selling_trading": "Korean equity short-selling trading",
    "kr_stock_lending_detail": "Korean equity securities lending",
    "kr_dividend_event": "Korean equity dividend events",
    "kr_treasury_yield": "Korean Treasury yields",
    "kospi200_futures_price": "KOSPI200 futures prices and provider observations",
    "kospi200_options_price": "KOSPI200 option prices and provider observations",
    "kospi200_futures_investor_flow": "KOSPI200 futures investor trading flow",
    "us_cftc_positions": "U.S. CFTC commitments-of-traders positions",
}


_DIRECT_GUI = frozenset("""
    kr_corp_code_map kr_fundamentals_quarterly
    market_price_60m_observation
    kr_index_daily kr_kospi200_index_daily global_index_price_daily global_etf_price_daily
    global_equity_price_daily tossinvest_us_quote_30m cboe_daily_pcr_daily
    kbsec_transactions_daily
    bok_ecos_kr_treasury_yield_source_observation bok_ecos_usd_krw_daily
    fred_treasury_yield_daily fred_usd_fx_daily
    global_commodity_futures_daily kr_market_breadth_daily
    kr_kospi200_breadth_daily
    kr_market_investor_net_purchase_bridge_daily fred_vix_daily kr_vkospi_daily
    kr_kospi200_option_pcr_daily kr_kospi200_option_walls_daily
    kr_kospi200_futures_nearest_listed_daily
    kr_kospi200_futures_investor_net_purchase_daily kr_equity_price_daily
    kr_equity_price_provisional_daily tossinvest_us_quote_30m
    kr_etf_master
    kr_short_selling_trading_daily kr_short_selling_balance_daily kr_stock_lending_daily
    kr_stock_lending_market_daily ls_t8462_daily_raw
""".split())

# Status visibility is an explicit consumer contract. Never infer it from the
# executable operations registry: collection registration is a separate axis.
_STATUS_ONLY_GUI = frozenset("""
kb_investor_flow_snapshot kr_credit_balance_daily kr_derivatives_futures_daily
kr_derivatives_options_daily kr_equity_canonical_universe_daily
kr_equity_market_cap_daily kr_equity_universe_daily kr_index_constituent_daily
kr_etf_price_daily
kr_index_fundamental_daily kr_kospi200_constituent_price_daily
kr_kospi200_futures_provider_bridge_daily kr_kospi200_options_provider_bridge_daily
kr_market_liquidity_daily kr_short_selling_investor_daily
kr_stock_lending_participant_daily us_treasury_spread_daily us_vix_term_structure_daily
""".split())


_INTENTIONALLY_EXCLUDED = frozenset("""
    market_price_60m_observation market_price_15m_observation
    krx_legacy_kospi200_futures_daily krx_legacy_kospi200_options_daily
    kr_market_investor_net_purchase_daily kr_equity_short_selling_daily
    kr_equity_program_trading_daily kr_equity_securities_lending_daily
    kr_equity_credit_trading_daily kr_investor_flow_daily
    kr_kospi200_futures_investor_trading_daily kr_kospi200_options_investor_trading_daily
    ls_t8428_surrounding_funds_source_observation
    us_cftc_legacy_futures_only_raw us_cftc_legacy_futures_options_combined_raw
    us_cftc_tff_futures_only_raw us_cftc_disaggregated_futures_only_raw
    kr_equity_foreign_ownership_daily kr_equity_fundamental_daily kr_etf_universe_daily
    kr_etf_ohlcv_daily kr_index_fundamental_daily kr_credit_benchmark_yield_daily
    kr_equity_sector_classification
    ls_t1633_program_trading_candidate research_target_price_consensus
""".split())


_PIT_SAFE = frozenset("""
    kr_kospi200_index_daily krx_legacy_kospi200_futures_daily
    krx_legacy_kospi200_options_daily kr_kospi200_futures_provider_bridge_daily
    kr_kospi200_options_provider_bridge_daily kr_kospi200_futures_nearest_listed_daily
    kr_kospi200_option_pcr_daily kr_kospi200_option_walls_daily
    kr_index_constituent_daily kr_kospi200_constituent_price_daily
    kr_kospi200_breadth_daily
""".split())


_NON_PREDICTIVE = frozenset("""
    kb_market_breadth_snapshot kb_program_trading_snapshot kb_investor_flow_snapshot
    kb_market_liquidity_snapshot kb_derivatives_summary_snapshot kb_domestic_index_snapshot
    kb_global_symbol_snapshot ls_t1633_program_trading_candidate ls_t8462_daily_raw
    kr_equity_price_provisional_daily
    cboe_daily_pcr_daily
    kbsec_transactions_daily
""".split())


_DERIVED_IDS = frozenset({
    "kr_market_breadth_daily", "us_treasury_spread_daily",
    "us_vix_term_structure_daily",
    "kr_kospi200_futures_nearest_listed_daily", "kr_kospi200_option_pcr_daily",
    "kr_kospi200_option_walls_daily",
    "kr_kospi200_breadth_daily",
})
_PUBLISHED_BRIDGE_IDS = frozenset({
    "kr_equity_canonical_universe_daily", "kr_market_investor_net_purchase_bridge_daily",
    "kr_kospi200_futures_provider_bridge_daily", "kr_kospi200_options_provider_bridge_daily",
    "kr_kospi200_constituent_price_daily",
})
_HISTORICAL_SEGMENT_IDS = frozenset({
    "krx_legacy_kospi200_futures_daily", "krx_legacy_kospi200_options_daily",
    "kr_market_investor_net_purchase_daily",
})
_RETAINED_HISTORY_ONLY_IDS = frozenset({
    "market_price_60m_observation", "market_price_15m_observation",
})
_SOURCE_OBSERVATION_IDS = frozenset({
    "market_price_60m_observation", "market_price_15m_observation",
    "bok_ecos_kr_treasury_yield_source_observation", "kr_equity_dividend",
    "kr_equity_dividend_source_observation", "kr_equity_rights_schedule",
    "kr_equity_stock_issuance_source_observation", "ls_t8428_surrounding_funds_source_observation",
})
_EVENT_IDS = frozenset({
    "kr_fundamentals_quarterly",
    "kr_equity_master", "kr_equity_dividend", "kr_equity_rights_schedule",
    "kr_equity_dividend_source_observation", "kr_equity_stock_issuance_source_observation",
})
_HEALTH_REFERENCE_IDS = _EVENT_IDS | frozenset({"kr_corp_code_map"})
_CADENCE_OVERRIDES = {
    "kr_etf_master": "daily",
    "kr_fundamentals_quarterly": "weekly",
}
_STATIC_COMPLETE_IDS = _HISTORICAL_SEGMENT_IDS | _RETAINED_HISTORY_ONLY_IDS | frozenset({
    "us_cftc_legacy_futures_only_raw", "us_cftc_legacy_futures_options_combined_raw",
    "us_cftc_tff_futures_only_raw", "us_cftc_disaggregated_futures_only_raw",
})
_MANUAL_RESEARCH_IDS = frozenset({
    "kr_equity_fundamental_daily", "kr_etf_universe_daily", "kr_etf_ohlcv_daily",
    "kr_credit_benchmark_yield_daily",
    "ls_t1633_program_trading_candidate",
    "kr_equity_short_selling_daily", "kr_equity_program_trading_daily",
    "kr_equity_securities_lending_daily", "kr_equity_credit_trading_daily",
    "kr_investor_flow_daily",
    "research_target_price_consensus",
})
_DISABLED_PENDING_CONTRACT_IDS = frozenset({
    "kr_derivatives_futures_daily", "kr_derivatives_options_daily",
    "kr_kosdaq150_futures_daily", "kr_kosdaq150_options_daily",
    "kr_kospi200_futures_investor_trading_daily",
    "kr_kospi200_options_investor_trading_daily",
    "ls_t8428_surrounding_funds_source_observation",
    "kr_equity_sector_classification",
})
_FINALITY_GATE_IDS = frozenset({
    "kr_equity_price_daily", "kr_equity_market_cap_daily", "kr_equity_universe_daily",
    "kr_equity_canonical_universe_daily", "kr_market_breadth_daily",
    "kr_index_daily", "kr_kospi200_index_daily", "kr_index_fundamental_daily",
    "kr_vkospi_daily", "kr_stock_lending_daily", "kr_stock_lending_market_daily",
    "kr_stock_lending_participant_daily", "kr_short_selling_balance_daily",
    "kr_short_selling_investor_daily",
    "kr_market_investor_net_purchase_bridge_daily",
    "ls_t8462_daily_raw",
    "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
    "kr_kospi200_breadth_daily",
})
_READY_WITH_LIMITS_IDS = frozenset({
    "global_index_price_daily", "global_etf_price_daily", "global_equity_price_daily",
    "global_commodity_futures_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily",
    "kr_etf_master", "kr_etf_price_daily", "kr_equity_price_provisional_daily",
    "kr_equity_investor_flow_daily",
    "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
    "bok_ecos_usd_krw_daily",
    "us_treasury_spread_daily", "us_vix_term_structure_daily",
    "kr_treasury_yield_daily",
    "bok_ecos_kr_treasury_yield_source_observation",
    "kr_market_investor_net_purchase_bridge_daily",
    "kr_market_investor_trading_daily",
    "kr_short_selling_trading_daily",
    "kr_market_liquidity_daily", "kr_credit_balance_daily",
    "kr_corp_code_map", "kr_fundamentals_quarterly",
})
_IMPLEMENTATION_READY_IDS: frozenset[str] = frozenset()
_READY_IDS = frozenset({
    "kr_kospi200_futures_daily", "kr_kospi200_options_daily",
    "kr_kospi200_futures_provider_bridge_daily",
    "kr_kospi200_options_provider_bridge_daily",
    "kr_kospi200_futures_nearest_listed_daily",
    "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
})
_INTENTIONAL_NO_OPERATION_IDS = frozenset({
    "kr_equity_short_selling_daily", "kr_equity_program_trading_daily",
    "kr_equity_securities_lending_daily", "kr_equity_credit_trading_daily",
    "kr_investor_flow_daily",
})
_BLOCK_REASONS = {
    **{dataset_id: OperationalBlockerReason.PERMISSION for dataset_id in """
        kr_derivatives_futures_daily kr_derivatives_options_daily
        kr_kosdaq150_futures_daily kr_kosdaq150_options_daily
        kr_kospi200_futures_investor_trading_daily
        kr_kospi200_options_investor_trading_daily
    """.split()},
    "ls_t8428_surrounding_funds_source_observation": OperationalBlockerReason.SEMANTICS,
    "kr_equity_sector_classification": OperationalBlockerReason.SEMANTICS,
}


_HEALTH_PRESERVATION_REASONS: Mapping[str, str] = MappingProxyType({
    "bok_ecos_kr_treasury_yield_source_observation": "수동 발행·확정성 관측",
    "kr_equity_foreign_ownership_daily": "수동 수집 전용",
    "kr_kospi200_futures_investor_net_purchase_daily": "수동 수집 전용",
    "kr_etf_ohlcv_daily": "kr_etf_price_daily로 대체됨",
    "kr_etf_universe_daily": "kr_etf_master로 대체됨",
    "krx_legacy_kospi200_futures_daily": "레거시 보관본",
    "krx_legacy_kospi200_options_daily": "레거시 보관본",
    "kr_kosdaq150_futures_daily": "레거시 보관본",
    "kr_kosdaq150_options_daily": "레거시 보관본",
    "research_target_price_consensus": "웹 화면용 수동 참고값",
})


def _health_preservation_reason(
    dataset_id: str,
    *,
    automation_enabled: bool,
    refresh_policy: RefreshPolicy,
    operational_status: UniverseOperationalStatus,
) -> str | None:
    if dataset_id in {"kr_corp_code_map", "kr_fundamentals_quarterly"}:
        return "주간 공시 갱신·최근 보존 이벤트"
    if dataset_id in _HEALTH_REFERENCE_IDS:
        return None
    if dataset_id in _HEALTH_PRESERVATION_REASONS:
        return _HEALTH_PRESERVATION_REASONS[dataset_id]
    if automation_enabled:
        return None
    if refresh_policy is RefreshPolicy.STATIC_COMPLETE:
        return "과거 자료 보관본"
    if operational_status is UniverseOperationalStatus.BLOCKED:
        return "계약 확인 전 수집 중지"
    if refresh_policy is RefreshPolicy.MANUAL_RESEARCH:
        return "연구 근거 보관본"
    return "수동 수집 전용"


def classify_health_display(
    spec: DatasetUniverseSpec,
    *,
    latest: str | None,
    expected: str | None,
    freshness: str,
    runtime_coverage: str = "NOT_PROBED",
    last_run: object = None,
) -> tuple[HealthDisplayStatus, str]:
    """Return one truthful web grade without turning retained data into incidents."""

    if spec.dataset_id in _HEALTH_REFERENCE_IDS:
        return HealthDisplayStatus.REFERENCE, "최근 보존 이벤트·기간"
    if spec.health_preservation_reason is not None:
        return HealthDisplayStatus.PRESERVED, spec.health_preservation_reason
    last_run_text = (
        " ".join(str(value) for value in last_run.values())
        if isinstance(last_run, Mapping)
        else str(last_run or "")
    ).upper()
    if any(token in last_run_text for token in ("FAIL", "ERROR", "BLOCKED")):
        return HealthDisplayStatus.FAILED, "마지막 실행 실패"
    if (
        spec.scheduler_lane == "TOSSINVEST_US_QUOTES_30M"
        and latest is not None
        and freshness in {"CURRENT", "EXPECTED_LAG", "UNKNOWN"}
    ):
        return HealthDisplayStatus.CURRENT, "최근 30분 경계 관측 보존"
    try:
        latest_value = date.fromisoformat(latest) if latest else None
        expected_value = date.fromisoformat(expected) if expected else None
    except ValueError:
        latest_value = expected_value = None
    if latest_value is not None and expected_value is not None:
        if latest_value >= expected_value:
            return HealthDisplayStatus.CURRENT, "최신일이 예상일 이상"
        if spec.automation_enabled and spec.scheduler_lane != "NO_SCHEDULER_LANE":
            return HealthDisplayStatus.LATE, "활성 자동화가 예상일보다 늦음"
    if freshness in {"CURRENT", "EXPECTED_LAG"}:
        return HealthDisplayStatus.CURRENT, "제공처 발행 정책 내 정상"
    if freshness == "STALE" and spec.automation_enabled:
        return HealthDisplayStatus.LATE, "활성 자동화가 예상일보다 늦음"
    return HealthDisplayStatus.PRESERVED, "실행 가능한 신선도 기준 없음"


_PHYSICAL_OVERRIDES = {
    "kbsec_transactions_daily": (
        "landing/kbsec/transactions/date=<YYYY-MM-DD>/<run_id>",
        "data/state/kbsec_transactions_daily/state.json",
        "artifacts/local_user/cash_flows.json",
        "artifacts/scheduler_logs/STOCK_DATA_KB_TRANSACTIONS_DAILY_last.json",
    ),
    "cboe_daily_pcr_daily": (
        "landing/cboe/daily_pcr/date=<YYYY-MM-DD>/<run_id>",
        "normalized/cboe_daily_pcr_daily",
        "artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json",
    ),
    "tossinvest_us_quote_30m": (
        "normalized/tossinvest_us_quote_30m",
        "artifacts/intraday/tossinvest_us_quotes_latest.json",
    ),
    "kr_equity_investor_flow_daily": (
        "landing/kr_equity_investor_flow_daily/<run_id>",
        "normalized/kr_equity_investor_flow_daily",
    ),
    "research_target_price_consensus": (
        "landing/research/target_prices/<run_id>",
        "normalized/research_target_price_consensus",
    ),
    "kr_corp_code_map": (
        "landing/opendart/kr_fundamentals_quarterly",
        "normalized/kr_corp_code_map",
    ),
    "kr_fundamentals_quarterly": (
        "landing/opendart/kr_fundamentals_quarterly",
        "normalized/kr_fundamentals_quarterly",
    ),
    "kr_vkospi_daily": ("raw/kr_vkospi_daily", "normalized/kr_vkospi_daily"),
    "kr_kospi200_futures_provider_bridge_daily": ("published/c007_kospi200_derivatives_bridge/kr_kospi200_futures_provider_bridge_daily",),
    "kr_kospi200_options_provider_bridge_daily": ("published/c007_kospi200_derivatives_bridge/kr_kospi200_options_provider_bridge_daily",),
    "kr_kospi200_option_walls_daily": (
        "artifacts/analysis/kospi200_option_wall_recent_250.csv",
    ),
    "global_commodity_futures_daily": ("normalized/global_commodity_futures_daily",),
    "ls_t8428_surrounding_funds_source_observation": ("landing/ls/t8428_surrounding_funds_raw",),
    "us_cftc_legacy_futures_only_raw": ("landing/cftc/legacy_cot_historical_raw::LEGACY_FUTURES_ONLY",),
    "us_cftc_legacy_futures_options_combined_raw": ("landing/cftc/legacy_cot_historical_raw::LEGACY_FUTURES_OPTIONS_COMBINED",),
    "us_cftc_tff_futures_only_raw": ("landing/cftc/cot_historical_raw::TFF_FUTURES_ONLY",),
    "us_cftc_disaggregated_futures_only_raw": ("landing/cftc/cot_historical_raw::DISAGGREGATED_FUTURES_ONLY",),
    "kr_equity_foreign_ownership_daily": ("landing/pykrx/high_value_raw/kr_equity_foreign_ownership_daily",),
    "kr_equity_fundamental_daily": ("landing/pykrx/high_value_raw/kr_equity_fundamental_daily",),
    "kr_etf_universe_daily": ("landing/pykrx/high_value_raw/kr_etf_universe_daily",),
    "kr_etf_ohlcv_daily": ("landing/pykrx/high_value_raw/kr_etf_universe_daily",),
    "kr_index_fundamental_daily": (
        "landing/diagnostics/pykrx_fundamentals_pilot::index_fundamental",
        "landing/kr_index_fundamental_daily",
        "normalized/kr_index_fundamental_daily",
    ),
    "kr_credit_benchmark_yield_daily": ("landing/diagnostics/pykrx_fundamentals_pilot::credit_benchmark",),
    "kr_index_constituent_daily": (
        "landing/diagnostics/pykrx_fundamentals_pilot::index_constituent",
        "landing/krx_mdc/kr_index_constituent_daily",
        "normalized/kr_index_constituent_daily",
    ),
    "kr_equity_sector_classification": ("landing/diagnostics/pykrx_fundamentals_pilot::sector_classification",),
    "ls_t1633_program_trading_candidate": ("landing/ls/t1633_program_trading_raw",),
    "ls_t8462_daily_raw": ("landing/ls_openapi/t8462_raw", "landing/ls_openapi/t8462_daily_raw"),
}


def _classification(dataset_id: str) -> DatasetRefreshClass:
    matches = [kind for kind, ids in _CLASSIFICATION_IDS.items() if dataset_id in ids]
    if len(matches) != 1:
        raise RuntimeError(f"dataset must have exactly one refresh class: {dataset_id}: {matches}")
    return matches[0]


def _economic_variable(dataset_id: str, fallback: str) -> str:
    matches = [group for group, ids in _ECONOMIC_GROUPS.items() if dataset_id in ids]
    if len(matches) > 1:
        raise RuntimeError(f"dataset belongs to multiple economic-variable groups: {dataset_id}")
    return _ECONOMIC_LABELS[matches[0]] if matches else fallback


def _scheduler_group(management: SchedulerManagement) -> SchedulerGroup:
    return {
        SchedulerManagement.DIRECT_COLLECTION_MANAGED: SchedulerGroup.DAILY_API_COLLECTION_REQUIRED,
        SchedulerManagement.DEPENDENCY_REFRESH_MANAGED: SchedulerGroup.DAILY_DERIVED_REFRESH,
        SchedulerManagement.WEEKLY_MANAGED: SchedulerGroup.WEEKLY,
        SchedulerManagement.EVENT_MANAGED: SchedulerGroup.EVENT_DRIVEN,
        SchedulerManagement.MANUAL_ONLY: SchedulerGroup.DAILY_API_COLLECTION_REQUIRED,
        SchedulerManagement.NO_REFRESH: SchedulerGroup.NO_REFRESH_REQUIRED,
        SchedulerManagement.RESEARCH: SchedulerGroup.RESEARCH_ONLY,
        SchedulerManagement.BLOCKED: SchedulerGroup.BLOCKED,
    }[management]


def _data_role(dataset_id: str) -> DataRole:
    if dataset_id in _DERIVED_IDS:
        return DataRole.DERIVED
    if dataset_id in _PUBLISHED_BRIDGE_IDS:
        return DataRole.PUBLISHED_BRIDGE
    if dataset_id in _HISTORICAL_SEGMENT_IDS:
        return DataRole.HISTORICAL_SEGMENT
    if dataset_id in _NON_CONTRACT:
        return DataRole.RAW_OBSERVATION
    if dataset_id.endswith("_snapshot"):
        return DataRole.SNAPSHOT
    if dataset_id in _SOURCE_OBSERVATION_IDS:
        return DataRole.SOURCE_OBSERVATION
    return DataRole.SOURCE


def _data_grain(dataset_id: str, cadence: str) -> DataGrain:
    if dataset_id.startswith("us_cftc_"):
        return DataGrain.WEEKLY
    return {
        "daily": DataGrain.DAILY,
        "intraday": DataGrain.INTRADAY,
        "event": DataGrain.EVENT_DRIVEN,
        "intraday_snapshot": DataGrain.SNAPSHOT,
        "research_only": DataGrain.DAILY,
        "weekly": DataGrain.WEEKLY,
        "quarterly": DataGrain.EVENT_DRIVEN,
    }.get(cadence, DataGrain.NONE)


def _refresh_policy(dataset_id: str, role: DataRole) -> RefreshPolicy:
    if role in {DataRole.DERIVED, DataRole.PUBLISHED_BRIDGE}:
        return RefreshPolicy.UPSTREAM_DEPENDENCY
    if role is DataRole.SNAPSHOT:
        return RefreshPolicy.SNAPSHOT_CAPTURE
    if dataset_id in _EVENT_IDS:
        return RefreshPolicy.APPEND_EVENT
    if dataset_id in _STATIC_COMPLETE_IDS:
        return RefreshPolicy.STATIC_COMPLETE
    if dataset_id in _MANUAL_RESEARCH_IDS:
        return RefreshPolicy.MANUAL_RESEARCH
    if dataset_id in _DISABLED_PENDING_CONTRACT_IDS:
        return RefreshPolicy.DISABLED_PENDING_CONTRACT
    return RefreshPolicy.GAP_FILL


def _operational_status(dataset_id: str, policy: RefreshPolicy) -> UniverseOperationalStatus:
    if dataset_id in _BLOCK_REASONS:
        return UniverseOperationalStatus.BLOCKED
    if dataset_id in _INTENTIONAL_NO_OPERATION_IDS:
        return UniverseOperationalStatus.NOT_APPLICABLE
    if policy is RefreshPolicy.STATIC_COMPLETE:
        return UniverseOperationalStatus.NOT_APPLICABLE
    if dataset_id in _READY_IDS:
        return UniverseOperationalStatus.READY
    if dataset_id in _FINALITY_GATE_IDS:
        return UniverseOperationalStatus.READY_WITH_FINALITY_GATE
    if dataset_id in _READY_WITH_LIMITS_IDS:
        return UniverseOperationalStatus.READY_WITH_LIMITS
    if dataset_id in _IMPLEMENTATION_READY_IDS:
        return UniverseOperationalStatus.IMPLEMENTATION_READY
    return UniverseOperationalStatus.MANUAL_ONLY


def _predictive_status(dataset_id: str, operation: object | None) -> PredictivePitStatus:
    if dataset_id in {
        "ls_t1633_program_trading_candidate", "research_target_price_consensus",
    }:
        return PredictivePitStatus.RESEARCH_ONLY
    if dataset_id in _NON_PREDICTIVE:
        return PredictivePitStatus.NON_PREDICTIVE
    if operation is not None:
        value = getattr(getattr(operation, "pit_status"), "value")
        return PredictivePitStatus(value) if value in PredictivePitStatus else PredictivePitStatus.PIT_BLOCKED
    if dataset_id in _PIT_SAFE:
        return PredictivePitStatus.PIT_SAFE
    return PredictivePitStatus.PIT_BLOCKED


_AUTO_ENABLED_IDS: frozenset[str] = frozenset({
    "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
    "bok_ecos_usd_krw_daily",
    "us_treasury_spread_daily", "us_vix_term_structure_daily",
    "kr_stock_lending_daily", "kr_stock_lending_market_daily",
    "kr_stock_lending_participant_daily",
    "kr_vkospi_daily",
    "kr_index_daily", "kr_kospi200_index_daily", "kr_index_fundamental_daily",
    "global_etf_price_daily", "global_equity_price_daily", "tossinvest_us_quote_30m",
    "kr_etf_master", "kr_etf_price_daily", "kr_equity_investor_flow_daily",
    "global_index_price_daily", "global_commodity_futures_daily",
    "kr_market_investor_net_purchase_bridge_daily",
    "kr_short_selling_trading_daily",
    "kr_equity_canonical_universe_daily", "kr_equity_price_daily",
    "kr_equity_price_provisional_daily",
    "kr_equity_market_cap_daily", "kr_equity_universe_daily",
    "kr_market_breadth_daily",
    "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
    "kr_kospi200_breadth_daily",
    "kr_kospi200_futures_daily", "kr_kospi200_options_daily",
    "kr_kospi200_futures_provider_bridge_daily",
    "kr_kospi200_options_provider_bridge_daily",
    "kr_kospi200_futures_nearest_listed_daily",
    "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
    "kr_market_liquidity_daily", "kr_credit_balance_daily",
    "kr_short_selling_balance_daily", "kr_short_selling_investor_daily",
    "ls_t8462_daily_raw", "kr_treasury_yield_daily",
    "bok_ecos_kr_treasury_yield_source_observation",
    "kr_market_investor_trading_daily",
    "kr_corp_code_map", "kr_fundamentals_quarterly",
    "cboe_daily_pcr_daily",
})


def _automation_policy(
    dataset_id: str, role: DataRole, refresh_policy: RefreshPolicy,
    operational_status: UniverseOperationalStatus,
) -> AutomationPolicy:
    if role in {DataRole.DERIVED, DataRole.PUBLISHED_BRIDGE}:
        return AutomationPolicy.DEPENDENCY_DRIVEN
    if refresh_policy is RefreshPolicy.STATIC_COMPLETE:
        return AutomationPolicy.NO_REFRESH
    if refresh_policy is RefreshPolicy.MANUAL_RESEARCH:
        return AutomationPolicy.RESEARCH_ONLY
    if operational_status is UniverseOperationalStatus.BLOCKED:
        return AutomationPolicy.DISABLED
    if dataset_id in _AUTO_ENABLED_IDS:
        return AutomationPolicy.AUTO_ELIGIBLE
    return AutomationPolicy.MANUAL_GATE


def _scheduler_management(
    role: DataRole,
    grain: DataGrain,
    refresh_policy: RefreshPolicy,
    operational_status: UniverseOperationalStatus,
) -> SchedulerManagement:
    if refresh_policy is RefreshPolicy.STATIC_COMPLETE:
        return SchedulerManagement.NO_REFRESH
    if refresh_policy is RefreshPolicy.MANUAL_RESEARCH:
        return SchedulerManagement.RESEARCH
    if operational_status is UniverseOperationalStatus.BLOCKED:
        return SchedulerManagement.BLOCKED
    if role in {DataRole.DERIVED, DataRole.PUBLISHED_BRIDGE}:
        return SchedulerManagement.DEPENDENCY_REFRESH_MANAGED
    if refresh_policy is RefreshPolicy.APPEND_EVENT:
        return SchedulerManagement.EVENT_MANAGED
    if grain is DataGrain.WEEKLY:
        return SchedulerManagement.WEEKLY_MANAGED
    if operational_status is UniverseOperationalStatus.MANUAL_ONLY:
        return SchedulerManagement.MANUAL_ONLY
    return SchedulerManagement.DIRECT_COLLECTION_MANAGED


def _lane(dataset_id: str) -> str:
    matches = [lane for lane, ids in _LANE_GROUPS.items() if dataset_id in ids]
    if len(matches) > 1:
        raise RuntimeError(f"dataset belongs to multiple scheduler lanes: {dataset_id}")
    return matches[0] if matches else "NO_SCHEDULER_LANE"


def build_dataset_universe(operations_registry: Mapping[str, object]) -> DatasetUniverseRegistry:
    dataset_ids = set(CONTRACTS) | set(_NON_CONTRACT)
    classified_ids = set().union(*_CLASSIFICATION_IDS.values())
    if dataset_ids != classified_ids:
        raise RuntimeError(
            f"dataset universe classification mismatch: missing={sorted(dataset_ids-classified_ids)}, "
            f"extra={sorted(classified_ids-dataset_ids)}"
        )
    if not _DIRECT_GUI.isdisjoint(_STATUS_ONLY_GUI):
        raise RuntimeError("direct and status-only GUI contracts overlap")
    unknown_gui_ids = (_DIRECT_GUI | _STATUS_ONLY_GUI) - dataset_ids
    if unknown_gui_ids:
        raise RuntimeError(f"GUI consumer contracts contain unknown datasets: {sorted(unknown_gui_ids)}")
    downstream: dict[str, list[str]] = {dataset_id: [] for dataset_id in dataset_ids}
    for child, parents in _UPSTREAM.items():
        for parent in parents:
            downstream[parent].append(child)
    specs = []
    operations_registry_size = len(operations_registry)
    for dataset_id in dataset_ids:
        contract = CONTRACTS.get(dataset_id)
        if contract is None:
            description, layer, source = _NON_CONTRACT[dataset_id]
            contract_version = None
            cadence = "weekly" if dataset_id.startswith("us_cftc_") else "research_only"
        else:
            description, layer, source = contract.description, contract.layer, contract.source
            contract_version, cadence = contract.version, contract.frequency
        cadence = _CADENCE_OVERRIDES.get(dataset_id, cadence)
        refresh_class = _classification(dataset_id)  # deprecated compatibility projection
        operation = operations_registry.get(dataset_id)
        registered = operation is not None
        if registered:
            disposition = RegistryDisposition.REGISTERED
            reason = None
        elif dataset_id in _INTENTIONALLY_EXCLUDED:
            disposition = RegistryDisposition.INTENTIONALLY_EXCLUDED
            reason = (
                f"retained historical dataset intentionally excluded from the "
                f"{operations_registry_size}-row current dataset operations registry; "
                "current display is governed separately by "
                "docs/data/operations/GLOBAL_MARKET_CURRENT_60M.md and writes no Normalized history"
                if dataset_id in _RETAINED_HISTORY_ONLY_IDS else
                f"historical/research/provider-specific dataset intentionally excluded "
                f"from the {operations_registry_size}-row current dataset operations registry"
            )
        else:
            disposition = RegistryDisposition.REGISTRY_MISSING
            reason = (
                "contracted or retained operational dataset was absent from the "
                f"{operations_registry_size}-row current dataset operations registry"
            )
        role = _data_role(dataset_id)
        grain = _data_grain(dataset_id, cadence)
        refresh_policy = _refresh_policy(dataset_id, role)
        operational_status = _operational_status(dataset_id, refresh_policy)
        blocker_reason = _BLOCK_REASONS.get(dataset_id)
        pit = _predictive_status(dataset_id, operation)
        automation_policy = _automation_policy(dataset_id, role, refresh_policy, operational_status)
        automation_enabled = dataset_id in _AUTO_ENABLED_IDS
        management = _scheduler_management(role, grain, refresh_policy, operational_status)
        if dataset_id in {"ls_t8462_daily_raw", "research_target_price_consensus"}:
            gui_use = GuiUse.DESCRIPTIVE
        elif dataset_id in _DIRECT_GUI:
            gui_use = GuiUse.DIRECT
        elif dataset_id in _STATUS_ONLY_GUI:
            gui_use = GuiUse.STATUS_ONLY
        else:
            gui_use = GuiUse.NONE
        coverage_start, retained_latest = _COVERAGE.get(dataset_id, (None, None))
        retained = dataset_id in _COVERAGE
        display_eligibility, display_reason = _display_decision(gui_use)
        research_eligibility, research_reason = _research_decision(
            retained=retained, contract_version=contract_version,
        )
        predictive_eligibility, predictive_reason = _predictive_decision(pit)
        physical = _PHYSICAL_OVERRIDES.get(dataset_id)
        if physical is None and retained:
            physical = (f"{layer}/{dataset_id}",)
        specs.append(DatasetUniverseSpec(
            dataset_id=dataset_id,
            economic_variable=_economic_variable(dataset_id, description),
            layer=layer,
            source=source,
            coverage_start=coverage_start,
            retained_latest=retained_latest,
            contract_version=contract_version,
            data_role=role,
            data_grain=grain,
            refresh_policy=refresh_policy,
            operational_status=operational_status,
            operational_blocker_reason=blocker_reason,
            predictive_pit_status=pit,
            automation_policy=automation_policy,
            automation_enabled=automation_enabled,
            scheduler_lane=_lane(dataset_id),
            gui_use=gui_use,
            display_consumer_eligibility=display_eligibility,
            display_consumer_reason=display_reason,
            research_consumer_eligibility=research_eligibility,
            research_consumer_reason=research_reason,
            predictive_consumer_eligibility=predictive_eligibility,
            predictive_consumer_reason=predictive_reason,
            scheduler_management=management,
            health_preservation_reason=_health_preservation_reason(
                dataset_id,
                automation_enabled=automation_enabled,
                refresh_policy=refresh_policy,
                operational_status=operational_status,
            ),
            primary_classification=refresh_class,
            secondary_roles=(DatasetRefreshClass.BLOCKED,) if pit is PredictivePitStatus.PIT_BLOCKED and refresh_class is not DatasetRefreshClass.BLOCKED else (),
            operations_registry_present_before=registered,
            operations_registry_entry=dataset_id if registered else None,
            registry_present=True,
            registry_entry=dataset_id,
            cadence=cadence,
            lane=_lane(dataset_id),
            upstream_dependencies=tuple(_UPSTREAM.get(dataset_id, ())),
            downstream_dependencies=tuple(sorted(downstream[dataset_id])),
            automation_required=refresh_policy in {
                RefreshPolicy.GAP_FILL, RefreshPolicy.UPSTREAM_DEPENDENCY,
                RefreshPolicy.APPEND_EVENT, RefreshPolicy.SNAPSHOT_CAPTURE,
            },
            prior_disposition=disposition,
            reason_if_not_registered_before=reason,
            scheduler_group=_scheduler_group(management),
            retained=retained,
            physical_artifacts=physical or (),
        ))
    return DatasetUniverseRegistry(specs)


__all__ = [
    "AutomationPolicy", "ConsumerEligibility", "ConsumerReasonCode",
    "DataGrain", "DataRole", "DatasetRefreshClass",
    "DatasetUniverseRegistry", "DatasetUniverseSpec", "GuiUse", "HealthDisplayStatus",
    "OperationalBlockerReason", "PredictivePitStatus", "RefreshPolicy",
    "RegistryDisposition", "SchedulerGroup", "SchedulerManagement",
    "UniverseOperationalStatus", "DATASET_SYMBOL_REGISTRY", "build_dataset_universe",
    "classify_health_display",
    "validate_consumer_decision",
]
