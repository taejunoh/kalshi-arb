import logging
from typing import Optional

logger = logging.getLogger("sum_arb")

MIN_DEPTH_USDC = 50.0


class SumArbStrategy:
    def __init__(self, config, order_manager, market_scanner, polymarket_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = polymarket_client

    def evaluate(self, yes_price, no_price, yes_depth, no_depth) -> Optional[dict]:
        token_sum = yes_price + no_price
        gap = 1.0 - token_sum
        if gap < self.config.SUM_ARB_MIN_GAP:
            return None
        if yes_depth < MIN_DEPTH_USDC or no_depth < MIN_DEPTH_USDC:
            return None
        fee_yes = self.config.estimate_taker_fee(yes_price)
        fee_no = self.config.estimate_taker_fee(no_price)
        total_fee = fee_yes + fee_no
        net_edge = gap - total_fee
        if net_edge <= 0:
            return None
        return {
            "yes_price": yes_price, "no_price": no_price,
            "gap": gap, "total_fee": total_fee, "net_edge": net_edge,
        }

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active():
            return
        markets = self._scanner.get_unlocked_markets()
        for market in markets:
            try:
                await self._check_market(market)
            except Exception as e:
                logger.error(f"Sum arb error on {market.get('conditionId')}: {e}")

    async def _check_market(self, market):
        condition_id = market["conditionId"]
        yes_token, no_token = self._client.get_token_ids(market)
        if not yes_token or not no_token:
            return
        yes_price = self._client.get_last_trade_price(yes_token)
        no_price = self._client.get_last_trade_price(no_token)
        if yes_price is None or no_price is None:
            return
        yes_depth = self._client.get_order_book_depth(yes_token)
        no_depth = self._client.get_order_book_depth(no_token)
        signal = self.evaluate(yes_price, no_price, yes_depth, no_depth)
        if signal is None:
            return
        size_usdc = self._order_manager.calculate_position_size(signal["net_edge"])
        if size_usdc < 1.0:
            return
        self._scanner.lock_market(condition_id)
        logger.info(
            f"[SUM ARB] {condition_id}: YES={signal['yes_price']:.3f} NO={signal['no_price']:.3f} "
            f"gap={signal['gap']:.4f} net_edge={signal['net_edge']:.4f} size=${size_usdc:.2f}"
        )
        yes_half = size_usdc / 2
        no_half = size_usdc / 2
        await self._order_manager.market_order(
            token_id=yes_token, side="BUY", size_usdc=yes_half,
            price=yes_price, strategy="sum_arb", market_id=condition_id,
        )
        await self._order_manager.market_order(
            token_id=no_token, side="BUY", size_usdc=no_half,
            price=no_price, strategy="sum_arb", market_id=condition_id,
        )
