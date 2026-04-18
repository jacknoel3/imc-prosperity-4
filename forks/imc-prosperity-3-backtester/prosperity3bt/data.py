from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from prosperity3bt.datamodel import Symbol, Trade
from prosperity3bt.file_reader import FileReader

DEFAULT_DENOMINATION = "XIRECS"
NON_ALGORITHMIC_PRODUCTS = {"DRYLAND_FLAX", "EMBER_MUSHROOM"}
OBSERVATION_PRODUCTS = {
    4: "MAGNIFICENT_MACARONS",
    5: "MAGNIFICENT_MACARONS",
}

LIMITS = {
    "RAINFOREST_RESIN": 50,
    "KELP": 50,
    "SQUID_INK": 50,
    "ASH_COATED_OSMIUM": 80,
    "INTARIAN_PEPPER_ROOT": 80,
    "CROISSANTS": 250,
    "JAMS": 350,
    "DJEMBES": 60,
    "PICNIC_BASKET1": 60,
    "PICNIC_BASKET2": 100,
    "VOLCANIC_ROCK": 400,
    "VOLCANIC_ROCK_VOUCHER_9500": 200,
    "VOLCANIC_ROCK_VOUCHER_9750": 200,
    "VOLCANIC_ROCK_VOUCHER_10000": 200,
    "VOLCANIC_ROCK_VOUCHER_10250": 200,
    "VOLCANIC_ROCK_VOUCHER_10500": 200,
    "MAGNIFICENT_MACARONS": 75,
}


@dataclass
class PriceRow:
    day: int
    timestamp: int
    product: Symbol
    bid_prices: list[int]
    bid_volumes: list[int]
    ask_prices: list[int]
    ask_volumes: list[int]
    mid_price: float
    profit_loss: float


def get_column_values(columns: list[str], indices: list[int]) -> list[int]:
    values = []

    for index in indices:
        value = columns[index]
        if value == "":
            break

        values.append(int(value))

    return values


@dataclass
class ObservationRow:
    timestamp: int
    bidPrice: float
    askPrice: float
    transportFees: float
    exportTariff: float
    importTariff: float
    sugarPrice: float
    sunlightIndex: float


@dataclass
class BacktestData:
    round_num: int
    day_num: int

    prices: dict[int, dict[Symbol, PriceRow]]
    trades: dict[int, dict[Symbol, list[Trade]]]
    observations: dict[int, ObservationRow]
    products: list[Symbol]
    profit_loss: dict[Symbol, float]
    denomination: str
    observation_product: Optional[Symbol]


def create_backtest_data(
    round_num: int,
    day_num: int,
    prices: list[PriceRow],
    trades: list[Trade],
    observations: list[ObservationRow],
    denomination: str = DEFAULT_DENOMINATION,
) -> BacktestData:
    prices_by_timestamp: dict[int, dict[Symbol, PriceRow]] = defaultdict(dict)
    for row in prices:
        prices_by_timestamp[row.timestamp][row.product] = row

    trades_by_timestamp: dict[int, dict[Symbol, list[Trade]]] = defaultdict(lambda: defaultdict(list))
    for trade in trades:
        trades_by_timestamp[trade.timestamp][trade.symbol].append(trade)

    products = sorted(set(row.product for row in prices))
    profit_loss = {product: 0.0 for product in products}

    observations_by_timestamp = {row.timestamp: row for row in observations}

    return BacktestData(
        round_num=round_num,
        day_num=day_num,
        prices=prices_by_timestamp,
        trades=trades_by_timestamp,
        observations=observations_by_timestamp,
        products=products,
        profit_loss=profit_loss,
        denomination=denomination,
        observation_product=OBSERVATION_PRODUCTS.get(round_num),
    )


def has_day_data(file_reader: FileReader, round_num: int, day_num: int) -> bool:
    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            return True

    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_combined.csv"]) as file:
        if file is None:
            return False

        for line in file.read_text(encoding="utf-8").splitlines()[1:]:
            if not line:
                continue

            columns = line.split(";")
            if parse_row_day(columns[0], columns[-1]) == day_num:
                return True

    return False


def parse_row_day(day_value: str, trailing_value: str) -> Optional[int]:
    if day_value != "":
        return int(day_value)

    if "," not in trailing_value:
        return None

    _, trailing_day = trailing_value.rsplit(",", 1)
    if trailing_day == "":
        return None

    return int(trailing_day)


def read_price_lines(file_reader: FileReader, round_num: int, day_num: int) -> list[str]:
    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            return file.read_text(encoding="utf-8").splitlines()[1:]

    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_combined.csv"]) as file:
        if file is None:
            raise ValueError(f"Prices data is not available for round {round_num} day {day_num}")

        lines = []
        for line in file.read_text(encoding="utf-8").splitlines()[1:]:
            if not line:
                continue

            columns = line.split(";")
            if parse_row_day(columns[0], columns[-1]) == day_num:
                lines.append(line)

        return lines


def read_trade_lines(file_reader: FileReader, round_num: int, day_num: int) -> list[str]:
    with file_reader.file([f"round{round_num}", f"trades_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            return file.read_text(encoding="utf-8").splitlines()[1:]

    with file_reader.file([f"round{round_num}", f"trades_round_{round_num}_combined.csv"]) as file:
        if file is None:
            return []

        lines = []
        for line in file.read_text(encoding="utf-8").splitlines()[1:]:
            if not line:
                continue

            columns = line.split(";")
            if parse_row_day("", columns[-1]) == day_num:
                lines.append(line)

        return lines


def read_day_data(file_reader: FileReader, round_num: int, day_num: int, no_names: bool) -> BacktestData:
    prices = []
    for line in read_price_lines(file_reader, round_num, day_num):
        columns = line.split(";")
        product = columns[2]
        if product in NON_ALGORITHMIC_PRODUCTS:
            continue

        raw_profit_loss = columns[16]
        profit_loss = raw_profit_loss.split(",", 1)[0]
        row_day = parse_row_day(columns[0], raw_profit_loss)

        prices.append(
            PriceRow(
                day=day_num if row_day is None else row_day,
                timestamp=int(columns[1]),
                product=product,
                bid_prices=get_column_values(columns, [3, 5, 7]),
                bid_volumes=get_column_values(columns, [4, 6, 8]),
                ask_prices=get_column_values(columns, [9, 11, 13]),
                ask_volumes=get_column_values(columns, [10, 12, 14]),
                mid_price=float(columns[15]),
                profit_loss=float(profit_loss),
            )
        )

    trades = []
    denominations = set()
    for line in read_trade_lines(file_reader, round_num, day_num):
        columns = line.split(";")
        symbol = columns[3]
        if symbol in NON_ALGORITHMIC_PRODUCTS:
            continue

        quantity = columns[6].split(",", 1)[0]
        currency = columns[4] or DEFAULT_DENOMINATION
        denominations.add(currency)

        trades.append(
            Trade(
                symbol=symbol,
                price=int(float(columns[5])),
                quantity=int(quantity),
                buyer=columns[1],
                seller=columns[2],
                timestamp=int(columns[0]),
            )
        )

    observations = []
    with file_reader.file([f"round{round_num}", f"observations_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            for line in file.read_text(encoding="utf-8").splitlines()[1:]:
                columns = line.split(",")

                observations.append(
                    ObservationRow(
                        timestamp=int(columns[0]),
                        bidPrice=float(columns[1]),
                        askPrice=float(columns[2]),
                        transportFees=float(columns[3]),
                        exportTariff=float(columns[4]),
                        importTariff=float(columns[5]),
                        sugarPrice=float(columns[6]),
                        sunlightIndex=float(columns[7]),
                    )
                )

    denomination = next(iter(denominations), DEFAULT_DENOMINATION)
    return create_backtest_data(round_num, day_num, prices, trades, observations, denomination)
