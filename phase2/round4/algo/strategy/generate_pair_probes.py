from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "counterparties_product_level_mapping.csv"
OUT_DIR = ROOT / "strategy"


LIMITS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    "VEV_4000": 300,
    "VEV_4500": 300,
    "VEV_5000": 300,
    "VEV_5100": 300,
    "VEV_5200": 300,
    "VEV_5300": 300,
    "VEV_5400": 300,
    "VEV_5500": 300,
    "VEV_6000": 300,
    "VEV_6500": 300,
}


PRODUCT_ORDER = {
    "HYDROGEL_PACK": 0,
    "VELVETFRUIT_EXTRACT": 1,
    "VEV_4000": 4000,
    "VEV_4500": 4500,
    "VEV_5000": 5000,
    "VEV_5100": 5100,
    "VEV_5200": 5200,
    "VEV_5300": 5300,
    "VEV_5400": 5400,
    "VEV_5500": 5500,
    "VEV_6000": 6000,
    "VEV_6500": 6500,
}


def mark_slug(mark: str) -> str:
    return mark.lower().replace(" ", "")


def pair_file_name(buyer: str, seller: str) -> str:
    return f"probe_pair_{mark_slug(buyer)}_{mark_slug(seller)}.py"


def load_pairs() -> dict[tuple[str, str], list[dict[str, str]]]:
    pairs: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
    with DATA_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            buyer = row["Buyer"]
            seller = row["Seller"]
            product = row["Product"]
            pairs.setdefault((buyer, seller), {})[product] = row
    return {
        pair: sorted(rows.values(), key=lambda row: PRODUCT_ORDER.get(row["Product"], 999999))
        for pair, rows in pairs.items()
    }


def render_strategy(buyer: str, seller: str, rows: list[dict[str, str]]) -> str:
    products = [row["Product"] for row in rows]
    products_repr = "[\n" + "".join(f'        "{p}",\n' for p in products) + "    ]"
    limits_repr = "{\n" + "".join(f'        "{p}": {LIMITS[p]},\n' for p in LIMITS) + "    }"
    pair_label = f"{buyer}->{seller}"
    file_label = pair_file_name(buyer, seller)
    hypotheses = "\n".join(
        "  "
        + f'- {row["Product"]}: observed_trades={row["Trades"]}, total_qty={row["Total_Qty"]}, '
        + f'avg_price={row["Avg_Price"]}. {row.get("Interpretation", "").strip()}'
        for row in rows
    )
    return f'''from __future__ import annotations

"""
{file_label}

Pair-specific counterparty probe for {pair_label}.

Purpose:
- This is an experimental data-collection strategy, not a production PnL bot.
- It targets only the products historically traded by {buyer} as buyer and
  {seller} as seller in the round 4 public data.
- We cannot directly choose the counterparty on Prosperity. Instead, this bot
  creates market conditions that are likely to expose the pair's behavior:
  passive sell bait for the historical buyer, passive buy bait for the
  historical seller, small taker probes into the visible book, and controlled
  inventory unwind.
- After upload, convert the log and measure fills by counterparty, product,
  side, quoted distance, size, and post-trade markout. The relevant question is
  whether {buyer} or {seller} appears in our own trades, at which prices, and
  whether those fills are toxic or profitable after 10/50/100/500 ticks.

Risk design:
- Small per-order size and a strict probe inventory cap keep the experiment
  from turning into a directional bet.
- Taker probes are deliberately tiny but frequent enough to guarantee log data
  when the book is available.
- If inventory approaches the probe cap, the strategy switches to aggressive
  flattening before continuing the experiment.

Historical hypotheses to confirm or reject:
{hypotheses}
"""

import json
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ModuleNotFoundError:
    from prosperity3bt.datamodel import Order, OrderDepth, TradingState


class Trader:
    TARGET_BUYER = "{buyer}"
    TARGET_SELLER = "{seller}"
    PAIR_LABEL = "{pair_label}"
    TARGET_PRODUCTS = {products_repr}
    LIMITS = {limits_repr}

    def bid(self) -> int:
        return 15

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {{product: [] for product in state.order_depths}}
        timestamp = int(getattr(state, "timestamp", 0))
        mode = (timestamp // 3000) % 6
        size_bucket = 1 + ((timestamp // 1000) % 3)
        mode_name = [
            "SYMMETRIC_PASSIVE",
            "SELL_BAIT_FOR_TARGET_BUYER",
            "BUY_BAIT_FOR_TARGET_SELLER",
            "TAKE_ASK_IDENTIFY_RESTING_SELLER",
            "HIT_BID_IDENTIFY_RESTING_BUYER",
            "INVENTORY_FLATTEN",
        ][mode]

        fill_notes = self._own_fill_notes(state)
        order_notes: List[str] = []

        for product in self.TARGET_PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            bid, ask = self._best_bid_ask(depth)
            if bid is None or ask is None:
                continue

            position = int(state.position.get(product, 0))
            max_probe_pos = self._max_probe_position(product)
            qty = self._base_qty(product, size_bucket)

            orders: List[Order] = []
            if abs(position) >= int(0.75 * max_probe_pos) or mode == 5:
                orders.extend(self._flatten_orders(product, bid, ask, position, qty))
            elif mode == 0:
                orders.extend(self._symmetric_passive(product, bid, ask, position, qty))
            elif mode == 1:
                orders.extend(self._sell_bait(product, bid, ask, position, qty))
            elif mode == 2:
                orders.extend(self._buy_bait(product, bid, ask, position, qty))
            elif mode == 3:
                orders.extend(self._take_ask(product, ask, position, max(1, qty // 2)))
            elif mode == 4:
                orders.extend(self._hit_bid(product, bid, position, max(1, qty // 2)))

            if orders:
                result[product].extend(orders)
                order_notes.append(product + ":" + ",".join(str(order) for order in orders))

        if fill_notes or timestamp % 3000 == 0:
            print(
                "PAIR_PROBE"
                + f"|pair={{self.PAIR_LABEL}}|mode={{mode_name}}|t={{timestamp}}"
                + f"|fills={{';'.join(fill_notes[:8])}}"
                + f"|orders={{';'.join(order_notes[:6])}}"
            )

        trader_data = json.dumps(
            {{
                "pair": self.PAIR_LABEL,
                "mode": mode_name,
                "t": timestamp,
                "products": self.TARGET_PRODUCTS,
            }},
            separators=(",", ":"),
        )
        return result, 0, trader_data

    def _own_fill_notes(self, state: TradingState) -> List[str]:
        notes: List[str] = []
        for product in self.TARGET_PRODUCTS:
            for trade in state.own_trades.get(product, []):
                buyer = getattr(trade, "buyer", "")
                seller = getattr(trade, "seller", "")
                price = getattr(trade, "price", "")
                qty = getattr(trade, "quantity", "")
                notes.append(f"{{product}}:{{buyer}}>{{seller}}@{{price}}x{{qty}}")
        return notes

    def _best_bid_ask(self, depth: Optional[OrderDepth]) -> Tuple[Optional[int], Optional[int]]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None, None
        return max(depth.buy_orders), min(depth.sell_orders)

    def _base_qty(self, product: str, bucket: int) -> int:
        if product == "HYDROGEL_PACK":
            return 4 * bucket
        if product == "VELVETFRUIT_EXTRACT":
            return 3 * bucket
        if product in {{"VEV_6000", "VEV_6500"}}:
            return 5 * bucket
        if product.startswith("VEV_"):
            return 2 * bucket
        return bucket

    def _max_probe_position(self, product: str) -> int:
        if product == "HYDROGEL_PACK":
            return 60
        if product == "VELVETFRUIT_EXTRACT":
            return 60
        if product in {{"VEV_6000", "VEV_6500"}}:
            return 90
        return 70

    def _buy_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] - position, self._max_probe_position(product) - position))

    def _sell_capacity(self, product: str, position: int) -> int:
        return max(0, min(self.LIMITS[product] + position, self._max_probe_position(product) + position))

    def _inside_prices(self, bid: int, ask: int) -> Tuple[int, int]:
        spread = ask - bid
        if spread >= 4:
            return bid + 1, ask - 1
        if spread == 3:
            return bid + 1, ask - 1
        return bid, ask

    def _symmetric_passive(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_px, sell_px = self._inside_prices(bid, ask)
        orders: List[Order] = []
        buy_qty = min(qty, self._buy_capacity(product, position))
        sell_qty = min(qty, self._sell_capacity(product, position))
        if buy_qty > 0 and buy_px < ask:
            orders.append(Order(product, buy_px, buy_qty))
        if sell_qty > 0 and sell_px > bid:
            orders.append(Order(product, sell_px, -sell_qty))
        return orders

    def _sell_bait(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        _, sell_px = self._inside_prices(bid, ask)
        sell_qty = min(qty * 2, self._sell_capacity(product, position))
        if sell_qty <= 0 or sell_px <= bid:
            return []
        return [Order(product, sell_px, -sell_qty)]

    def _buy_bait(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        buy_px, _ = self._inside_prices(bid, ask)
        buy_qty = min(qty * 2, self._buy_capacity(product, position))
        if buy_qty <= 0 or buy_px >= ask:
            return []
        return [Order(product, buy_px, buy_qty)]

    def _take_ask(self, product: str, ask: int, position: int, qty: int) -> List[Order]:
        buy_qty = min(qty, self._buy_capacity(product, position))
        if buy_qty <= 0:
            return []
        return [Order(product, ask, buy_qty)]

    def _hit_bid(self, product: str, bid: int, position: int, qty: int) -> List[Order]:
        sell_qty = min(qty, self._sell_capacity(product, position))
        if sell_qty <= 0:
            return []
        return [Order(product, bid, -sell_qty)]

    def _flatten_orders(self, product: str, bid: int, ask: int, position: int, qty: int) -> List[Order]:
        if position > 0:
            sell_qty = min(abs(position), max(qty * 2, 1), self.LIMITS[product] + position)
            return [Order(product, bid, -sell_qty)] if sell_qty > 0 else []
        if position < 0:
            buy_qty = min(abs(position), max(qty * 2, 1), self.LIMITS[product] - position)
            return [Order(product, ask, buy_qty)] if buy_qty > 0 else []
        return self._symmetric_passive(product, bid, ask, position, max(1, qty // 2))
'''


def main() -> None:
    pairs = load_pairs()
    manifest_rows = []
    for (buyer, seller), rows in pairs.items():
        path = OUT_DIR / pair_file_name(buyer, seller)
        path.write_text(render_strategy(buyer, seller, rows))
        manifest_rows.append(
            {
                "file": path.name,
                "buyer": buyer,
                "seller": seller,
                "products": "|".join(row["Product"] for row in rows),
                "historical_trades": str(sum(int(row["Trades"]) for row in rows)),
                "historical_total_qty": str(sum(int(row["Total_Qty"]) for row in rows)),
            }
        )
        print(path)
    manifest_path = OUT_DIR / "probe_pair_manifest.csv"
    with manifest_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "buyer",
                "seller",
                "products",
                "historical_trades",
                "historical_total_qty",
            ],
        )
        writer.writeheader()
        writer.writerows(manifest_rows)
    print(manifest_path)


if __name__ == "__main__":
    main()
