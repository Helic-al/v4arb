import math
import os
import time

import requests
from dotenv import load_dotenv
from logger import setup_logger

load_dotenv("./.env")

# --- 定数 (Arbitrum One) ---
NFPM_ADDR = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
SWAP_ROUTER_ADDR = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
WETH_ADDR = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

# 簡易ABI (必要な関数のみ)
NFPM_ABI = '[{"inputs":[{"internalType":"uint256","name":"tokenId","type":"uint256"}],"name":"positions","outputs":[{"internalType":"uint96","name":"nonce","type":"uint96"},{"internalType":"address","name":"operator","type":"address"},{"internalType":"address","name":"token0","type":"address"},{"internalType":"address","name":"token1","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"int24","name":"tickLower","type":"int24"},{"internalType":"int24","name":"tickUpper","type":"int24"},{"internalType":"uint128","name":"liquidity","type":"uint128"},{"internalType":"uint256","name":"feeGrowthInside0LastX128","type":"uint256"},{"internalType":"uint256","name":"feeGrowthInside1LastX128","type":"uint256"},{"internalType":"uint128","name":"tokensOwed0","type":"uint128"},{"internalType":"uint128","name":"tokensOwed1","type":"uint128"}],"stateMutability":"view","type":"function"},{"inputs":[{"components":[{"internalType":"uint256","name":"tokenId","type":"uint256"},{"internalType":"uint128","name":"liquidity","type":"uint128"},{"internalType":"uint256","name":"amount0Min","type":"uint256"},{"internalType":"uint256","name":"amount1Min","type":"uint256"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"internalType":"struct INonfungiblePositionManager.DecreaseLiquidityParams","name":"params","type":"tuple"}],"name":"decreaseLiquidity","outputs":[{"internalType":"uint256","name":"amount0","type":"uint256"},{"internalType":"uint256","name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"},{"inputs":[{"components":[{"internalType":"uint256","name":"tokenId","type":"uint256"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint128","name":"amount0Max","type":"uint128"},{"internalType":"uint128","name":"amount1Max","type":"uint128"}],"internalType":"struct INonfungiblePositionManager.CollectParams","name":"params","type":"tuple"}],"name":"collect","outputs":[{"internalType":"uint256","name":"amount0","type":"uint256"},{"internalType":"uint256","name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"},{"inputs":[{"components":[{"internalType":"address","name":"token0","type":"address"},{"internalType":"address","name":"token1","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"int24","name":"tickLower","type":"int24"},{"internalType":"int24","name":"tickUpper","type":"int24"},{"internalType":"uint256","name":"amount0Desired","type":"uint256"},{"internalType":"uint256","name":"amount1Desired","type":"uint256"},{"internalType":"uint256","name":"amount0Min","type":"uint256"},{"internalType":"uint256","name":"amount1Min","type":"uint256"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"internalType":"struct INonfungiblePositionManager.MintParams","name":"params","type":"tuple"}],"name":"mint","outputs":[{"internalType":"uint256","name":"tokenId","type":"uint256"},{"internalType":"uint128","name":"liquidity","type":"uint128"},{"internalType":"uint256","name":"amount0","type":"uint256"},{"internalType":"uint256","name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"},{"constant":true,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"index","type":"uint256"}],"name":"tokenOfOwnerByIndex","outputs":[{"name":"","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"}]'
ROUTER_ABI = '[{"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMinimum","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct ISwapRouter.ExactInputSingleParams","name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]'
ERC20_ABI = '[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]'

DISCORD_URL = os.environ.get("DISCORD_URL")


def sendDiscord(message):
    try:
        data = {"content": message}
        requests.post(DISCORD_URL, json=data)
    except:
        pass


class UniswapManager:
    def __init__(self, web3_instance, private_key):
        self.w3 = web3_instance
        self.account = self.w3.eth.account.from_key(private_key)
        self.my_address = self.account.address
        self.private_key = private_key

        # コントラクトの初期化
        self.nfpm = self.w3.eth.contract(address=NFPM_ADDR, abi=NFPM_ABI)
        self.router = self.w3.eth.contract(address=SWAP_ROUTER_ADDR, abi=ROUTER_ABI)
        self.weth = self.w3.eth.contract(address=WETH_ADDR, abi=ERC20_ABI)
        self.usdc = self.w3.eth.contract(address=USDC_ADDR, abi=ERC20_ABI)

        # nonce管理
        self._last_used_nonce = -1

        # loggerインスタンスを作成
        self.log = setup_logger("UniswapManager.log")

    def _get_next_nonce(self):
        network_nonce = self.w3.eth.get_transaction_count(self.my_address, "pending")

        if self._last_used_nonce == -1:
            current_nonce = network_nonce
        else:
            current_nonce = max(network_nonce, self._last_used_nonce + 1)

        self.last_used_nonce = current_nonce

        self.log.info(
            f"DEBUG: Network={network_nonce}, locallast={self._last_used_nonce}, using={current_nonce}"
        )
        sendDiscord(
            f"DEBUG: Network={network_nonce}, locallast={self._last_used_nonce}, using={current_nonce}"
        )

        return current_nonce

    def _send_tx(self, func_call, nonce=None, gas_limit=None, gas_multiplier=1.5):
        """
        堅牢なトランザクション送信ヘルパー
        - EIP-1559対応
        - Nonce手動管理対応
        - ガス見積もり自動化
        """
        try:
            # 1. Nonceの決定
            # 引数で渡されたらそれを使い、なければネットワークから取得
            if nonce is None:
                nonce = self._get_next_nonce()

            # 2. ガス代の計算 (EIP-1559 / Arbitrum最適化)
            # 最新ブロックのBaseFeeを取得
            block = self.w3.eth.get_block("latest")
            base_fee = block["baseFeePerGas"]

            # Priority Fee (チップ) - Arbitrumなら0.1 Gweiあれば十分だが、少し積む
            max_priority_fee = self.w3.to_wei(0.1, "gwei")

            # Max Fee (BaseFee * 倍率 + Chip)
            # 急な高騰に耐えられるようバッファを持たせる
            max_fee_per_gas = int(base_fee * gas_multiplier) + max_priority_fee

            # 3. Gas Limit (使用量) の見積もり
            # 失敗するTxならここでエラーが出るので、無駄なガス代を払わずに済む
            if gas_limit is None:
                try:
                    # 推定値の1.2倍程度を見込んでおく（安全策）
                    estimated_gas = func_call.estimate_gas({"from": self.my_address})
                    gas_limit = int(estimated_gas * 1.2)
                except Exception as e:
                    self.log.info(f"⚠️ Gas見積もり失敗(デフォルト値使用): {e}")
                    gas_limit = 2000000  # 失敗時は多めの固定値

            # 4. トランザクション構築
            tx_params = {
                "from": self.my_address,
                "nonce": nonce,
                "gas": gas_limit,
                "maxFeePerGas": max_fee_per_gas,
                "maxPriorityFeePerGas": max_priority_fee,
                "type": "0x2",  # EIP-1559形式
                "chainId": self.w3.eth.chain_id,  # チェーンIDも明示すると安全
            }

            # トランザクション辞書をビルド
            tx = func_call.build_transaction(tx_params)

            # 5. 署名と送信
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            self.log.info(f"🚀 Tx Sent: {tx_hash.hex()} (Nonce: {nonce})")

            # 6. 完了待ち (待機)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] == 1:
                self.log.info(f"✅ Tx Success: {tx_hash.hex()}")
                return receipt
            else:
                self.log.info(f"❌ Tx Reverted: {tx_hash.hex()}")
                # Revert理由の解析などをここに入れるとなお良し
                raise Exception(f"Transaction Reverted: {tx_hash.hex()}")

        except Exception as e:
            self.log.info(f"❌ Tx Error: {e}")
            raise e

    # --- 1. ポジションクローズ & 回収 ---
    def close_position(self, token_id):
        """流動性を抜き、手数料を回収する"""
        self.log.info(f"🗑️ Closing Position ID: {token_id}...")

        # 1-1. 現在の流動性量を取得
        pos = self.nfpm.functions.positions(token_id).call()
        liquidity = pos[7]

        if liquidity > 0:
            # 1-2. 流動性を解除 (Decrease Liquidity)
            params = {
                "tokenId": token_id,
                "liquidity": liquidity,  # 全額
                "amount0Min": 0,  # スリッページ許容 (Bot用なので簡易化)
                "amount1Min": 0,
                "deadline": int(time.time()) + 60,
            }
            self._send_tx(self.nfpm.functions.decreaseLiquidity(params))
            self.log.info("  -> Liquidity Decreased.")

        # 1-3. 資産と手数料を回収 (Collect)
        # MaxUint128を指定して「あるだけ全部」回収する
        MAX_UINT128 = 2**128 - 1
        collect_params = {
            "tokenId": token_id,
            "recipient": self.my_address,
            "amount0Max": MAX_UINT128,
            "amount1Max": MAX_UINT128,
        }
        self._send_tx(self.nfpm.functions.collect(collect_params))
        self.log.info("  -> Fees & Tokens Collected.")
        sendDiscord("  -> Fees & Tokens Collected.")

        # ※ Burnは必須ではないが、リストを綺麗にするなら行う。今回は省略。

    # --- 2. 資産バランス調整 (簡易版スワップ) ---
    def auto_swap_for_ratio(self, target_price_range_center, current_price):
        """
        新しいレンジの中心価格に合わせて、WETH/USDCの比率を調整する
        (厳密な計算は複雑なので、Bot用に「現在価格がレンジのどこにあるか」で簡易判定)
        """
        # 現在の残高確認
        eth_bal = self.weth.functions.balanceOf(self.my_address).call()
        usdc_bal = self.usdc.functions.balanceOf(self.my_address).call()

        eth_val_usd = (eth_bal / 1e18) * current_price
        usdc_val_usd = usdc_bal / 1e6
        total_usd = eth_val_usd + usdc_val_usd

        self.log.info(
            f"💰 Balance: {eth_bal / 1e18:.4f} ETH / {usdc_bal / 1e6:.2f} USDC (Total ${total_usd:.2f})"
        )

        # ターゲット比率 (現在価格がレンジの中心なら 50:50)
        # 簡易ロジック: 常に 50:50 を目指す (現在価格を中心としたレンジを作る場合)
        target_eth_usd = total_usd * 0.5

        # ETHが多すぎる -> ETHを売ってUSDCにする
        if eth_val_usd > target_eth_usd * 1.1:  # 10%以上の偏りがあれば
            sell_amount_usd = eth_val_usd - target_eth_usd
            sell_amount_eth = sell_amount_usd / current_price
            amount_in_wei = int(sell_amount_eth * 1e18)
            self.log.info(f"🔄 Swapping {sell_amount_eth:.4f} ETH to USDC...")
            self._swap(WETH_ADDR, USDC_ADDR, amount_in_wei, 500)  # 0.05% Pool

        # USDCが多すぎる -> USDCを売ってETHにする
        elif usdc_val_usd > total_usd * 0.5 * 1.1:
            sell_amount_usdc = usdc_val_usd - (total_usd * 0.5)
            amount_in_wei = int(sell_amount_usdc * 1e6)
            self.log.info(f"🔄 Swapping {sell_amount_usdc:.2f} USDC to ETH...")
            self._swap(USDC_ADDR, WETH_ADDR, amount_in_wei, 500)

        else:
            self.log.info("✅ Balance is good enough.")

    def _swap(self, token_in, token_out, amount_in, fee=500):
        """Swap実行"""
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "fee": fee,
            "recipient": self.my_address,
            "deadline": int(time.time()) + 60,
            "amountIn": amount_in,
            "amountOutMinimum": 0,
            "sqrtPriceLimitX96": 0,
        }
        self._send_tx(self.router.functions.exactInputSingle(params))

    # --- 3. 新規ポジション作成 ---
    def mint_new_position(self, tick_lower, tick_upper):
        """現在の全残高を使ってポジションを作成"""
        self.log.info(f"✨ Minting New Position: Tick {tick_lower} ~ {tick_upper}")

        # 残高再確認
        amount0_desired = self.weth.functions.balanceOf(self.my_address).call()
        amount1_desired = self.usdc.functions.balanceOf(self.my_address).call()

        # Approve (念のため毎回チェックするか、無限Approve済みなら省略可)
        # self._approve_token(self.weth, NFPM_ADDR, amount0_desired)
        # self._approve_token(self.usdc, NFPM_ADDR, amount1_desired)

        # 5%程度のバッファを残して投入 (全額指定だと計算誤差でFailしやすい)
        amount0_inject = int(amount0_desired * 0.97)
        amount1_inject = int(amount1_desired * 0.97)

        params = {
            "token0": WETH_ADDR,  # WETHの方がアドレスが若い場合が多いが要確認
            "token1": USDC_ADDR,  # Arbitrumでは USDC < WETH なので token0=USDC, token1=WETH になるケースに注意！
            # ※ Arbitrum Oneの場合:
            # USDC: 0xaf88...
            # WETH: 0x82aF...
            # アドレス順なので token0=WETH(0x82..), token1=USDC(0xaf..) が逆転しないか注意が必要
            # (以下は WETH < USDC と仮定していますが、実際のアドレス比較が必要)
            "fee": 500,  # 0.05%
            "tickLower": tick_lower,
            "tickUpper": tick_upper,
            "amount0Desired": amount0_inject,  # WETH
            "amount1Desired": amount1_inject,  # USDC
            "amount0Min": 0,
            "amount1Min": 0,
            "recipient": self.my_address,
            "deadline": int(time.time()) + 180,
        }

        # アドレス順序の修正 (WETH=0x82, USDC=0xaf なので WETH < USDC)
        # よって token0=WETH, token1=USDC で正しい

        self._send_tx(self.nfpm.functions.mint(params), gas_limit=3000000)

        # ログからTokenIDを取得
        # Transferイベントなどのログ解析が必要だが、簡易的に直近の所有トークンを取得する
        balance = self.nfpm.functions.balanceOf(self.my_address).call()
        new_token_id = self.nfpm.functions.tokenOfOwnerByIndex(
            self.my_address, balance - 1
        ).call()

        self.log.info(f"🎉 New Position Created! ID: {new_token_id}")
        sendDiscord(f"🎉 New Position Created! ID: {new_token_id}")
        return new_token_id

    # --- 統合メソッド ---
    def execute_rebalance(
        self, old_token_id, new_lower_price, new_upper_price, current_price
    ):
        """
        1. 古いポジションを解約
        2. スワップで比率調整
        3. 新しいポジション作成
        """
        # Tick変換
        tick_lower = self.price_to_tick(new_lower_price)
        tick_upper = self.price_to_tick(new_upper_price)
        # Tickのアライメント (Tick spacing 10 for 0.05% pool)
        tick_lower = (tick_lower // 10) * 10
        tick_upper = (tick_upper // 10) * 10

        # 1. Close
        if old_token_id:
            self.close_position(old_token_id)
            time.sleep(2)  # ブロック反映待ち

        # 2. Swap (Balance Adjust)
        # 新しいレンジの中心をターゲットにする
        target_center = (new_lower_price + new_upper_price) / 2
        self.auto_swap_for_ratio(target_center, current_price)
        time.sleep(2)

        # 3. Mint
        new_id = self.mint_new_position(tick_lower, tick_upper)

        return new_id

    def price_to_tick(self, price):
        """価格からTickを計算"""
        # price = 1.0001 ^ tick
        # tick = log_1.0001(price)
        # WETH/USDCの場合、1ETH = 3000 USDC とかなので
        # decimal調整: 1e18 / 1e6 = 1e12倍 の補正が必要
        adjusted_price = price / 1e12  # (WETH decimals - USDC decimals) の調整
        return int(math.log(adjusted_price, 1.0001))
