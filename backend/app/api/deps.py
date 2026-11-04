from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, Path, Request

from app.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]

SymbolPath = Annotated[str, Path(pattern=r"^[A-Z0-9]{2,20}USDT$", description="USDT perpetual symbol, e.g. BTCUSDT")]

Timeframe = Literal["5m", "15m", "30m", "1h", "4h", "1d", "1w"]
