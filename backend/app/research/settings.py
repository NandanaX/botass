"""Runtime settings of the decision engine (kept import-light for ``settings_store``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.research.entry_quality import EntryQualityParams
from app.research.portfolio import PortfolioParams
from app.research.regime import RegimeParams


class DecisionSettings(BaseModel):
    # shadow: record the decision and show it in Telegram, block nothing.
    # enforce: send Telegram alerts only for decisions that are QUALIFIED.
    mode: Literal["shadow", "enforce"] = "shadow"
    use_study_filters: bool = True
    portfolio: PortfolioParams = Field(default_factory=PortfolioParams)
    regime: RegimeParams = Field(default_factory=RegimeParams)
    entry_quality: EntryQualityParams = Field(default_factory=EntryQualityParams)
