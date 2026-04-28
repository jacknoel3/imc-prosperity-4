import csv
import hashlib
import math
import os
from contextlib import closing, redirect_stdout
from functools import lru_cache
from io import StringIO
from pathlib import Path

from IPython.utils.io import Tee
from tqdm import tqdm

from prosperity3bt.data import DEFAULT_LIMIT, LIMITS, BacktestData, read_day_data
from prosperity3bt.datamodel import (
    ConversionObservation,
    Listing,
    Observation,
    Order,
    OrderDepth,
    Symbol,
    Trade,
    TradingState,
)
from prosperity3bt.file_reader import FileReader
from prosperity3bt.models import (
    ActivityLogRow,
    BacktestResult,
    MarketTrade,
    SandboxLogRow,
    TradeMatchingMode,
    TradeRow,
)


PROFILE_FALLBACK_MULTIPLIER = 0.35


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def stable_unit_interval(*parts: object) -> float:
    raw = "|".join(map(str, parts)).encode("utf-8")
    digest = hashlib.blake2b(raw, digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def find_round4_profile_dir() -> Path | None:
    configured = os.environ.get("PROSPERITY4MCBT_R4_PROFILE_DIR")
    if configured:
        path = Path(configured)
        return path if path.is_dir() else None

    relative = Path("phase2/round4/algo/backtests/player_profile_current")
    for base in [Path.cwd(), *Path.cwd().parents]:
        candidate = base / relative
        if candidate.is_dir():
            return candidate

    return None


@lru_cache(maxsize=1)
def round4_fill_profile() -> dict[str, dict[tuple[str, ...], float]]:
    profile_dir = find_round4_profile_dir()
    counterparty: dict[tuple[str, str], float] = {}
    product: dict[tuple[str, str], float] = {}
    if profile_dir is None:
        return {"counterparty": counterparty, "product": product}

    counterparty_path = profile_dir / "bot_counterparty_exposure.csv"
    if counterparty_path.is_file():
        with counterparty_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                name = row.get("counterparty", "")
                side = row.get("submission_side", "")
                if not name or side not in {"BUY", "SELL"}:
                    continue
                qty = float(row.get("qty") or 0.0)
                fills = float(row.get("fills") or 0.0)
                # Quantity and fill count are realized probe exposure, not a true
                # opportunity denominator. Treat them as confidence/liquidity caps.
                counterparty[(name, side)] = max(
                    counterparty.get((name, side), 0.0),
                    clamp(0.20 + qty / 90.0 + fills / 180.0, 0.20, 1.0),
                )

    product_path = profile_dir / "bot_product_own_fills.csv"
    if product_path.is_file():
        with product_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                symbol = row.get("product", "")
                side = row.get("submission_side", "")
                if not symbol or side not in {"BUY", "SELL"}:
                    continue
                qty = float(row.get("qty") or 0.0)
                fills = float(row.get("fills") or 0.0)
                product[(symbol, side)] = max(
                    product.get((symbol, side), 0.0),
                    clamp(0.20 + qty / 160.0 + fills / 260.0, 0.20, 1.0),
                )

    return {"counterparty": counterparty, "product": product}


def profiled_multiplier(symbol: str, counterparty: str, submission_side: str) -> float:
    profile = round4_fill_profile()
    counterparty_score = profile["counterparty"].get(
        (counterparty, submission_side),
        PROFILE_FALLBACK_MULTIPLIER,
    )
    product_score = profile["product"].get(
        (symbol, submission_side),
        PROFILE_FALLBACK_MULTIPLIER,
    )
    return clamp(math.sqrt(counterparty_score * product_score), 0.05, 1.0)


def profiled_volume_cap(
    symbol: str,
    counterparty: str,
    submission_side: str,
    eligible_volume: int,
    timestamp: int,
    price: int,
) -> int:
    if eligible_volume <= 0:
        return 0
    multiplier = profiled_multiplier(symbol, counterparty, submission_side)
    target = eligible_volume * multiplier
    base = int(math.floor(target))
    if stable_unit_interval(symbol, counterparty, submission_side, timestamp, price, eligible_volume) < target - base:
        base += 1
    return min(eligible_volume, base)


def buy_queue_ahead(state: TradingState, order: Order) -> int:
    return sum(
        volume
        for price, volume in state.order_depths[order.symbol].buy_orders.items()
        if price >= order.price
    )


def sell_queue_ahead(state: TradingState, order: Order) -> int:
    return sum(
        abs(volume)
        for price, volume in state.order_depths[order.symbol].sell_orders.items()
        if price <= order.price
    )


def prepare_state(state: TradingState, data: BacktestData) -> None:
    for product in data.products:
        order_depth = OrderDepth()
        row = data.prices[state.timestamp][product]

        for price, volume in zip(row.bid_prices, row.bid_volumes):
            order_depth.buy_orders[price] = volume

        for price, volume in zip(row.ask_prices, row.ask_volumes):
            order_depth.sell_orders[price] = -volume

        state.order_depths[product] = order_depth

        state.listings[product] = Listing(product, product, 1)

    observation_row = data.observations.get(state.timestamp)

    if observation_row is None:
        state.observations = Observation({}, {})
    else:
        conversion_observation = ConversionObservation(
            bidPrice=observation_row.bidPrice,
            askPrice=observation_row.askPrice,
            transportFees=observation_row.transportFees,
            exportTariff=observation_row.exportTariff,
            importTariff=observation_row.importTariff,
            sugarPrice=observation_row.sugarPrice,
            sunlightIndex=observation_row.sunlightIndex,
        )

        state.observations = Observation(
            plainValueObservations={}, conversionObservations={"MAGNIFICENT_MACARONS": conversion_observation}
        )


def type_check_orders(orders: dict[Symbol, list[Order]]) -> None:
    for key, value in orders.items():
        if not isinstance(key, str):
            raise ValueError(f"Orders key '{key}' is of type {type(key)}, expected a str")

        for order in value:
            if not isinstance(order.symbol, str):
                raise ValueError(f"Order symbol of '{order}' is of type {type(order.symbol)}, expected a str")

            if not isinstance(order.price, int):
                raise ValueError(f"Order price of '{order}' is of type {type(order.price)}, expected an int")

            if not isinstance(order.quantity, int):
                raise ValueError(f"Order quantity of '{order}' is of type {type(order.quantity)}, expected an int")


def create_activity_logs(
    state: TradingState,
    data: BacktestData,
    result: BacktestResult,
) -> None:
    for product in data.products:
        row = data.prices[state.timestamp][product]

        product_profit_loss = data.profit_loss[product]

        position = state.position.get(product, 0)
        if position != 0:
            product_profit_loss += position * row.mid_price

        bid_prices_len = len(row.bid_prices)
        bid_volumes_len = len(row.bid_volumes)
        ask_prices_len = len(row.ask_prices)
        ask_volumes_len = len(row.ask_volumes)

        columns = [
            result.day_num,
            state.timestamp,
            product,
            row.bid_prices[0] if bid_prices_len > 0 else "",
            row.bid_volumes[0] if bid_volumes_len > 0 else "",
            row.bid_prices[1] if bid_prices_len > 1 else "",
            row.bid_volumes[1] if bid_volumes_len > 1 else "",
            row.bid_prices[2] if bid_prices_len > 2 else "",
            row.bid_volumes[2] if bid_volumes_len > 2 else "",
            row.ask_prices[0] if ask_prices_len > 0 else "",
            row.ask_volumes[0] if ask_volumes_len > 0 else "",
            row.ask_prices[1] if ask_prices_len > 1 else "",
            row.ask_volumes[1] if ask_volumes_len > 1 else "",
            row.ask_prices[2] if ask_prices_len > 2 else "",
            row.ask_volumes[2] if ask_volumes_len > 2 else "",
            row.mid_price,
            product_profit_loss,
        ]

        result.activity_logs.append(ActivityLogRow(columns))


def enforce_limits(
    state: TradingState,
    data: BacktestData,
    orders: dict[Symbol, list[Order]],
    sandbox_row: SandboxLogRow,
) -> None:
    sandbox_log_lines = []
    for product in data.products:
        product_orders = orders.get(product, [])
        product_position = state.position.get(product, 0)

        total_long = sum(order.quantity for order in product_orders if order.quantity > 0)
        total_short = sum(abs(order.quantity) for order in product_orders if order.quantity < 0)

        limit = LIMITS.get(product, DEFAULT_LIMIT)
        if product_position + total_long > limit or product_position - total_short < -limit:
            sandbox_log_lines.append(f"Orders for product {product} exceeded limit of {limit} set")
            orders.pop(product)

    if len(sandbox_log_lines) > 0:
        sandbox_row.sandbox_log += "\n" + "\n".join(sandbox_log_lines)


def match_buy_order(
    state: TradingState,
    data: BacktestData,
    order: Order,
    market_trades: list[MarketTrade],
    trade_matching_mode: TradeMatchingMode,
) -> list[Trade]:
    trades = []

    order_depth = state.order_depths[order.symbol]
    price_matches = sorted(price for price in order_depth.sell_orders.keys() if price <= order.price)
    for price in price_matches:
        volume = min(order.quantity, abs(order_depth.sell_orders[price]))

        trades.append(Trade(order.symbol, price, volume, "SUBMISSION", "", state.timestamp))

        state.position[order.symbol] = state.position.get(order.symbol, 0) + volume
        data.profit_loss[order.symbol] -= price * volume

        order_depth.sell_orders[price] += volume
        if order_depth.sell_orders[price] == 0:
            order_depth.sell_orders.pop(price)

        order.quantity -= volume
        if order.quantity == 0:
            return trades

    if trade_matching_mode == TradeMatchingMode.none:
        return trades

    for market_trade in market_trades:
        if (
            market_trade.sell_quantity == 0
            or market_trade.trade.price > order.price
            or (market_trade.trade.price == order.price and trade_matching_mode == TradeMatchingMode.worse)
        ):
            continue

        available_sell_quantity = market_trade.sell_quantity
        if trade_matching_mode == TradeMatchingMode.profiled:
            available_sell_quantity = max(0, available_sell_quantity - buy_queue_ahead(state, order))
            available_sell_quantity = profiled_volume_cap(
                order.symbol,
                market_trade.trade.seller,
                "BUY",
                available_sell_quantity,
                state.timestamp,
                order.price,
            )

        volume = min(order.quantity, available_sell_quantity)
        if volume <= 0:
            continue

        trades.append(
            Trade(order.symbol, order.price, volume, "SUBMISSION", market_trade.trade.seller, state.timestamp)
        )

        state.position[order.symbol] = state.position.get(order.symbol, 0) + volume
        data.profit_loss[order.symbol] -= order.price * volume

        market_trade.sell_quantity -= volume

        order.quantity -= volume
        if order.quantity == 0:
            return trades

    return trades


def match_sell_order(
    state: TradingState,
    data: BacktestData,
    order: Order,
    market_trades: list[MarketTrade],
    trade_matching_mode: TradeMatchingMode,
) -> list[Trade]:
    trades = []

    order_depth = state.order_depths[order.symbol]
    price_matches = sorted((price for price in order_depth.buy_orders.keys() if price >= order.price), reverse=True)
    for price in price_matches:
        volume = min(abs(order.quantity), order_depth.buy_orders[price])

        trades.append(Trade(order.symbol, price, volume, "", "SUBMISSION", state.timestamp))

        state.position[order.symbol] = state.position.get(order.symbol, 0) - volume
        data.profit_loss[order.symbol] += price * volume

        order_depth.buy_orders[price] -= volume
        if order_depth.buy_orders[price] == 0:
            order_depth.buy_orders.pop(price)

        order.quantity += volume
        if order.quantity == 0:
            return trades

    if trade_matching_mode == TradeMatchingMode.none:
        return trades

    for market_trade in market_trades:
        if (
            market_trade.buy_quantity == 0
            or market_trade.trade.price < order.price
            or (market_trade.trade.price == order.price and trade_matching_mode == TradeMatchingMode.worse)
        ):
            continue

        available_buy_quantity = market_trade.buy_quantity
        if trade_matching_mode == TradeMatchingMode.profiled:
            available_buy_quantity = max(0, available_buy_quantity - sell_queue_ahead(state, order))
            available_buy_quantity = profiled_volume_cap(
                order.symbol,
                market_trade.trade.buyer,
                "SELL",
                available_buy_quantity,
                state.timestamp,
                order.price,
            )

        volume = min(abs(order.quantity), available_buy_quantity)
        if volume <= 0:
            continue

        trades.append(Trade(order.symbol, order.price, volume, market_trade.trade.buyer, "SUBMISSION", state.timestamp))

        state.position[order.symbol] = state.position.get(order.symbol, 0) - volume
        data.profit_loss[order.symbol] += order.price * volume

        market_trade.buy_quantity -= volume

        order.quantity += volume
        if order.quantity == 0:
            return trades

    return trades


def match_order(
    state: TradingState,
    data: BacktestData,
    order: Order,
    market_trades: list[MarketTrade],
    trade_matching_mode: TradeMatchingMode,
) -> list[Trade]:
    if order.quantity > 0:
        return match_buy_order(state, data, order, market_trades, trade_matching_mode)
    elif order.quantity < 0:
        return match_sell_order(state, data, order, market_trades, trade_matching_mode)
    else:
        return []


def match_orders(
    state: TradingState,
    data: BacktestData,
    orders: dict[Symbol, list[Order]],
    result: BacktestResult,
    trade_matching_mode: TradeMatchingMode,
) -> None:
    market_trades = {
        product: [MarketTrade(t, t.quantity, t.quantity) for t in trades]
        for product, trades in data.trades[state.timestamp].items()
    }

    for product in data.products:
        new_trades = []

        for order in orders.get(product, []):
            new_trades.extend(
                match_order(
                    state,
                    data,
                    order,
                    market_trades.get(product, []),
                    trade_matching_mode,
                )
            )

        if len(new_trades) > 0:
            state.own_trades[product] = new_trades
            result.trades.extend([TradeRow(trade) for trade in new_trades])

    for product, trades in market_trades.items():
        for trade in trades:
            trade.trade.quantity = min(trade.buy_quantity, trade.sell_quantity)

        remaining_market_trades = [t.trade for t in trades if t.trade.quantity > 0]

        state.market_trades[product] = remaining_market_trades
        result.trades.extend([TradeRow(trade) for trade in remaining_market_trades])


def run_backtest(
    trader,
    file_reader: FileReader,
    round_num: int,
    day_num: int,
    print_output: bool,
    trade_matching_mode: TradeMatchingMode,
    no_names: bool,
    show_progress_bar: bool,
) -> BacktestResult:
    data = read_day_data(file_reader, round_num, day_num, no_names)

    os.environ["PROSPERITY3BT_ROUND"] = str(round_num)
    os.environ["PROSPERITY3BT_DAY"] = str(day_num)

    trader_data = ""
    state = TradingState(
        traderData=trader_data,
        timestamp=0,
        listings={},
        order_depths={},
        own_trades={},
        market_trades={},
        position={},
        observations=Observation({}, {}),
    )

    result = BacktestResult(
        round_num=data.round_num,
        day_num=data.day_num,
        sandbox_logs=[],
        activity_logs=[],
        trades=[],
    )

    timestamps = sorted(data.prices.keys())
    timestamps_iterator = tqdm(timestamps, ascii=True) if show_progress_bar else timestamps

    for timestamp in timestamps_iterator:
        state.timestamp = timestamp
        state.traderData = trader_data

        prepare_state(state, data)

        stdout = StringIO()

        # Tee calls stdout.close(), making stdout.getvalue() impossible
        # This override makes getvalue() possible after close()
        stdout.close = lambda: None  # type: ignore[method-assign]

        if print_output:
            with closing(Tee(stdout)):
                orders, conversions, trader_data = trader.run(state)
        else:
            with redirect_stdout(stdout):
                orders, conversions, trader_data = trader.run(state)

        sandbox_row = SandboxLogRow(
            timestamp=timestamp,
            sandbox_log="",
            lambda_log=stdout.getvalue().rstrip(),
        )

        result.sandbox_logs.append(sandbox_row)

        type_check_orders(orders)
        create_activity_logs(state, data, result)
        enforce_limits(state, data, orders, sandbox_row)
        match_orders(state, data, orders, result, trade_matching_mode)

    result.final_position = dict(state.position)
    return result
