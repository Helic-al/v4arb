"""
UniswapV4Manager - V4 流動性管理
==================================
UniswapManager.py (V3) をベースに V4 PositionManager 対応に書き換えたもの。

V3 → V4 の主な変更点:
  - NFPM (ERC-721) → PositionManager (multicall / Actions ベース)
  - SwapRouter → PoolSwapTest (テストネット用)
  - NFT Token ID → PoolKey + tick range + salt で識別

ターゲット: Arbitrum Sepolia Testnet
"""

import json
import math
import os
import time

import requests
from dotenv import load_dotenv
from logger import setup_logger
from web3 import Web3

load_dotenv("./.env")

# --- 定数 (Arbitrum Sepolia) ---
POSITION_MANAGER_ADDR = "0xAc631556d3d4019C95769033B5E719dD77124BAc"
POOL_SWAP_TEST_ADDR = "0xf3a39c86dbd13c45365e57fb90fe413371f65af8"
POOL_MANAGER_ADDR = "0xFB3e0C6F74eB1a21CC1Da29aeC80D2Dfe6C9a317"
STATE_VIEW_ADDR = "0x9d467fa9062b6e9b1a46e26007ad82db116c67cb"

# テストネット用トークンアドレス (実際のテストトークンに差し替えること)
WETH_ADDR = os.environ.get("WETH_ADDRESS", "")
USDC_ADDR = os.environ.get("USDC_ADDRESS", "")

# Hook アドレス (DeltaNeutralHookデプロイ後に設定)
HOOK_ADDR = os.environ.get("HOOK_ADDRESS", "")

# PoolKey パラメータ
POOL_FEE = int(os.environ.get("POOL_FEE", "8388608"))  # DYNAMIC_FEE_FLAG
TICK_SPACING = int(os.environ.get("TICK_SPACING", "60"))

ERC20_ABI = json.dumps([
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
])

# V4 PoolSwapTest ABI (テスト用swap)
POOL_SWAP_TEST_ABI = json.dumps([
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "Currency", "name": "currency0", "type": "address"},
                    {"internalType": "Currency", "name": "currency1", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
                    {"internalType": "IHooks", "name": "hooks", "type": "address"},
                ],
                "internalType": "struct PoolKey",
                "name": "key",
                "type": "tuple",
            },
            {
                "components": [
                    {"internalType": "bool", "name": "zeroForOne", "type": "bool"},
                    {"internalType": "int256", "name": "amountSpecified", "type": "int256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IPoolManager.SwapParams",
                "name": "params",
                "type": "tuple",
            },
            {
                "components": [
                    {"internalType": "bool", "name": "takeClaims", "type": "bool"},
                    {"internalType": "bool", "name": "settleUsingBurn", "type": "bool"},
                ],
                "internalType": "struct PoolSwapTest.TestSettings",
                "name": "testSettings",
                "type": "tuple",
            },
            {"internalType": "bytes", "name": "hookData", "type": "bytes"},
        ],
        "name": "swap",
        "outputs": [{"internalType": "BalanceDelta", "name": "delta", "type": "int256"}],
        "stateMutability": "payable",
        "type": "function",
    }
])

# V4 PoolModifyLiquidityTest ABI
POOL_MODIFY_LIQUIDITY_TEST_ADDR = "0x9a8ca723f5dccb7926d00b71dec55c2fea1f50f7"
POOL_MODIFY_LIQUIDITY_TEST_ABI = json.dumps([
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "Currency", "name": "currency0", "type": "address"},
                    {"internalType": "Currency", "name": "currency1", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
                    {"internalType": "IHooks", "name": "hooks", "type": "address"},
                ],
                "internalType": "struct PoolKey",
                "name": "key",
                "type": "tuple",
            },
            {
                "components": [
                    {"internalType": "int24", "name": "tickLower", "type": "int24"},
                    {"internalType": "int24", "name": "tickUpper", "type": "int24"},
                    {"internalType": "int256", "name": "liquidityDelta", "type": "int256"},
                    {"internalType": "bytes32", "name": "salt", "type": "bytes32"},
                ],
                "internalType": "struct IPoolManager.ModifyLiquidityParams",
                "name": "params",
                "type": "tuple",
            },
            {"internalType": "bytes", "name": "hookData", "type": "bytes"},
        ],
        "name": "modifyLiquidity",
        "outputs": [
            {"internalType": "BalanceDelta", "name": "delta", "type": "int256"},
            {"internalType": "BalanceDelta", "name": "feeDelta", "type": "int256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    }
])

DISCORD_URL = os.environ.get("DISCORD_URL")


def sendDiscord(message):
    try:
        data = {"content": message}
        requests.post(DISCORD_URL, json=data)
    except:
        pass


class UniswapV4Manager:
    """
    Uniswap V4 用の流動性管理クラス。
    テストネット (Arbitrum Sepolia) 向け。
    PoolSwapTest / PoolModifyLiquidityTest 経由でオペレーションを実行する。
    """

    def __init__(self, web3_instance, private_key):
        self.w3 = web3_instance
        self.account = self.w3.eth.account.from_key(private_key)
        self.my_address = self.account.address
        self.private_key = private_key

        # V4 テスト用コントラクト
        self.swap_test = self.w3.eth.contract(
            address=Web3.to_checksum_address(POOL_SWAP_TEST_ADDR),
            abi=json.loads(POOL_SWAP_TEST_ABI),
        )
        self.modify_liquidity_test = self.w3.eth.contract(
            address=Web3.to_checksum_address(POOL_MODIFY_LIQUIDITY_TEST_ADDR),
            abi=json.loads(POOL_MODIFY_LIQUIDITY_TEST_ABI),
        )

        # ERC20 トークン
        if WETH_ADDR:
            self.weth = self.w3.eth.contract(
                address=Web3.to_checksum_address(WETH_ADDR),
                abi=json.loads(ERC20_ABI),
            )
        if USDC_ADDR:
            self.usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDR),
                abi=json.loads(ERC20_ABI),
            )

        # Nonce管理
        self._last_used_nonce = -1

        # Logger
        self.log = setup_logger("UniswapV4Manager.log")

    def _get_pool_key(self):
        """PoolKey タプルを構築"""
        return (
            Web3.to_checksum_address(WETH_ADDR if WETH_ADDR < USDC_ADDR else USDC_ADDR),
            Web3.to_checksum_address(USDC_ADDR if WETH_ADDR < USDC_ADDR else WETH_ADDR),
            POOL_FEE,
            TICK_SPACING,
            Web3.to_checksum_address(HOOK_ADDR),
        )

    def _get_next_nonce(self):
        network_nonce = self.w3.eth.get_transaction_count(self.my_address, "pending")
        if self._last_used_nonce == -1:
            current_nonce = network_nonce
        else:
            current_nonce = max(network_nonce, self._last_used_nonce + 1)
        self._last_used_nonce = current_nonce
        return current_nonce

    def _send_tx(self, func_call, nonce=None, gas_limit=None, gas_multiplier=1.5):
        """
        堅牢なトランザクション送信ヘルパー
        - EIP-1559対応
        - Nonce手動管理
        - ガス見積もり自動化
        """
        try:
            if nonce is None:
                nonce = self._get_next_nonce()

            block = self.w3.eth.get_block("latest")
            base_fee = block["baseFeePerGas"]
            max_priority_fee = self.w3.to_wei(0.1, "gwei")
            max_fee_per_gas = int(base_fee * gas_multiplier) + max_priority_fee

            if gas_limit is None:
                try:
                    estimated_gas = func_call.estimate_gas({"from": self.my_address})
                    gas_limit = int(estimated_gas * 1.2)
                except Exception as e:
                    self.log.info(f"⚠️ Gas見積もり失敗(デフォルト値使用): {e}")
                    gas_limit = 2000000

            tx_params = {
                "from": self.my_address,
                "nonce": nonce,
                "gas": gas_limit,
                "maxFeePerGas": max_fee_per_gas,
                "maxPriorityFeePerGas": max_priority_fee,
                "type": "0x2",
                "chainId": self.w3.eth.chain_id,
            }

            tx = func_call.build_transaction(tx_params)
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            self.log.info(f"🚀 Tx Sent: {tx_hash.hex()} (Nonce: {nonce})")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] == 1:
                self.log.info(f"✅ Tx Success: {tx_hash.hex()}")
                return receipt
            else:
                self.log.info(f"❌ Tx Reverted: {tx_hash.hex()}")
                raise Exception(f"Transaction Reverted: {tx_hash.hex()}")

        except Exception as e:
            self.log.info(f"❌ Tx Error: {e}")
            raise e

    # ============================================================
    # 流動性の削除 (V4: modifyLiquidity with negative delta)
    # ============================================================
    def remove_liquidity(self, tick_lower, tick_upper, liquidity_amount, salt=b"\x00" * 32):
        """
        V4 では流動性の追加も削除も modifyLiquidity で行う。
        削除の場合は liquidityDelta を負にする。
        """
        self.log.info(
            f"🗑️ Removing Liquidity: Tick {tick_lower} ~ {tick_upper}, L={liquidity_amount}"
        )

        pool_key = self._get_pool_key()
        params = (
            tick_lower,
            tick_upper,
            -int(liquidity_amount),  # 負の値で流動性を削除
            salt,
        )

        self._send_tx(
            self.modify_liquidity_test.functions.modifyLiquidity(
                pool_key, params, b""
            )
        )
        self.log.info("  -> Liquidity Removed.")
        sendDiscord("  -> Liquidity Removed.")

    # ============================================================
    # 流動性の追加 (V4: modifyLiquidity with positive delta)
    # ============================================================
    def add_liquidity(self, tick_lower, tick_upper, liquidity_amount, salt=b"\x00" * 32):
        """
        V4 での流動性追加。
        """
        self.log.info(
            f"✨ Adding Liquidity: Tick {tick_lower} ~ {tick_upper}, L={liquidity_amount}"
        )

        pool_key = self._get_pool_key()
        params = (
            tick_lower,
            tick_upper,
            int(liquidity_amount),
            salt,
        )

        self._send_tx(
            self.modify_liquidity_test.functions.modifyLiquidity(
                pool_key, params, b""
            ),
            gas_limit=3000000,
        )
        self.log.info("  -> Liquidity Added.")
        sendDiscord("  -> Liquidity Added.")

    # ============================================================
    # スワップ (V4: PoolSwapTest 経由)
    # ============================================================
    def swap(self, zero_for_one, amount_specified):
        """
        V4 テスト用スワップ。
        zero_for_one: Token0→Token1 なら True
        amount_specified: 負の値 = exact input, 正の値 = exact output
        """
        self.log.info(
            f"🔄 Swap: zeroForOne={zero_for_one}, amount={amount_specified}"
        )

        pool_key = self._get_pool_key()

        # sqrtPriceLimitX96: 0 = no limit
        # V4標準的なリミット値
        import math as m
        if zero_for_one:
            sqrt_price_limit = 4295128739 + 1  # MIN_SQRT_PRICE + 1
        else:
            sqrt_price_limit = (
                1461446703485210103287273052203988822378723970342 - 1
            )  # MAX_SQRT_PRICE - 1

        swap_params = (
            zero_for_one,
            amount_specified,
            sqrt_price_limit,
        )

        test_settings = (
            False,  # takeClaims
            False,  # settleUsingBurn
        )

        self._send_tx(
            self.swap_test.functions.swap(
                pool_key, swap_params, test_settings, b""
            )
        )
        self.log.info("  -> Swap Complete.")
        sendDiscord("  -> Swap Complete.")

    # ============================================================
    # 資産バランス調整
    # ============================================================
    def auto_swap_for_ratio(self, current_price):
        """
        WETH/USDCの比率を50:50に調整する。
        """
        if not WETH_ADDR or not USDC_ADDR:
            self.log.warning("⚠️ Token addresses not configured")
            return

        eth_bal = self.weth.functions.balanceOf(self.my_address).call()
        usdc_bal = self.usdc.functions.balanceOf(self.my_address).call()

        eth_val_usd = (eth_bal / 1e18) * current_price
        usdc_val_usd = usdc_bal / 1e6
        total_usd = eth_val_usd + usdc_val_usd

        self.log.info(
            f"💰 Balance: {eth_bal / 1e18:.4f} ETH / {usdc_bal / 1e6:.2f} USDC (Total ${total_usd:.2f})"
        )

        target_eth_usd = total_usd * 0.5

        # ETHが多すぎる → Token0→Token1 方向のスワップ
        if eth_val_usd > target_eth_usd * 1.1:
            sell_amount_usd = eth_val_usd - target_eth_usd
            sell_amount_eth = sell_amount_usd / current_price
            amount_in_wei = int(sell_amount_eth * 1e18)
            self.log.info(f"🔄 Swapping {sell_amount_eth:.4f} ETH to USDC...")
            # token0がETHかUSDCかはアドレス順による
            zero_for_one = WETH_ADDR.lower() < USDC_ADDR.lower()
            self.swap(zero_for_one, -amount_in_wei)  # negative = exact input

        # USDCが多すぎる
        elif usdc_val_usd > total_usd * 0.5 * 1.1:
            sell_amount_usdc = usdc_val_usd - (total_usd * 0.5)
            amount_in_wei = int(sell_amount_usdc * 1e6)
            self.log.info(f"🔄 Swapping {sell_amount_usdc:.2f} USDC to ETH...")
            zero_for_one = USDC_ADDR.lower() < WETH_ADDR.lower()
            self.swap(zero_for_one, -amount_in_wei)

        else:
            self.log.info("✅ Balance is good enough.")

    # ============================================================
    # リバランス (クローズ → スワップ → ミント)
    # ============================================================
    def execute_rebalance(
        self,
        old_tick_lower,
        old_tick_upper,
        old_liquidity,
        new_lower_price,
        new_upper_price,
        current_price,
        salt=b"\x00" * 32,
    ):
        """
        V4でのリバランス:
        1. 古いポジションの流動性を削除
        2. スワップで比率調整
        3. 新しいティックレンジで流動性追加

        V3との違い: NFT IDではなく tick range + salt で識別
        """
        # Tick変換
        new_tick_lower = self.price_to_tick(new_lower_price)
        new_tick_upper = self.price_to_tick(new_upper_price)
        # TickSpacing でアライメント
        new_tick_lower = (new_tick_lower // TICK_SPACING) * TICK_SPACING
        new_tick_upper = (new_tick_upper // TICK_SPACING) * TICK_SPACING

        # 1. Close (流動性削除)
        if old_liquidity > 0:
            self.remove_liquidity(old_tick_lower, old_tick_upper, old_liquidity, salt)
            time.sleep(2)

        # 2. Swap (Balance Adjust)
        self.auto_swap_for_ratio(current_price)
        time.sleep(2)

        # 3. Mint (新しい流動性追加)
        # 新たに追加する流動性量は、スワップ後の残高から計算する必要がある
        # テストネットでは簡易的に固定値 or 元の流動性を再投入
        self.add_liquidity(new_tick_lower, new_tick_upper, old_liquidity, salt)

        self.log.info(
            f"🎉 Rebalance Complete! New Range: {new_lower_price:.0f} ~ {new_upper_price:.0f}"
        )
        sendDiscord(
            f"🎉 Rebalance Complete! Tick {new_tick_lower} ~ {new_tick_upper}"
        )

        return new_tick_lower, new_tick_upper

    # ============================================================
    # ユーティリティ
    # ============================================================
    def price_to_tick(self, price):
        """価格からTickを計算 (WETH/USDC ペア用)"""
        adjusted_price = price / 1e12  # decimals補正 (18-6)
        return int(math.log(adjusted_price, 1.0001))
