"""
Delta Neutral Bot - Uniswap V4 Version
=========================================
mainv6_7.py (V3) をベースに V4 アーキテクチャに対応させたもの。

V3 → V4 の主な変更点:
  - Pool.slot0()       → StateView.getSlot0(poolId)
  - NFPM.positions()   → StateView.getPositionInfo() / PositionManager
  - NFPM.collect()     → PositionManager.collect()
  - Pool Contract直接  → PoolManager + StateView 経由
  - NFT Token ID管理   → PositionManager の positionId (ERC-6909)
  - Hook (DeltaNeutralHook.sol) の MarketVolatile イベント監視

ターゲット: ローカル Anvil (Chain ID: 31337) / Arbitrum Sepolia (Chain ID: 421614)
"""

import datetime
import json
import math
import os
import threading
import time
from decimal import Decimal

import boto3
import eth_account
import requests
from dotenv import load_dotenv
from getSecret import get_secret_key
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
from logger import setup_logger
from lowPassFilter import LowPassFilter
from oorDetector import oorDetector
from PoolRepositioner import PoolRepositioner, isLiquidityZero
from v4PoolUtils import poolUtils
from web3 import Web3

load_dotenv("./.env")

# ログを残す
log = setup_logger("bot_log.log")
orderLog = setup_logger(name="OrderLogV4", log_file="bot_log.log")

# --- ユーザー設定 ---
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")  # TODO
MAIN_ACCOUNT_ADDRESS = os.environ.get("ARB_WALLET_ADDRESS")  # TODO
ARB_SECRET = get_secret_key()

THRESHOLD = 0.160  # 初期リバランス閾値
ALLOWABLE_RISK_PCT = 0.080  # 運用資金から許容するズレ(デルタETH)の割合
# TARGET_RATIO = 0.5  # しきい値の何割までデルタを打ち消すか
MAX_RETRY = 3  # 指値注文のリトライ回数
RECORD_TIME = 300  # dynamoDBへの記録間隔(秒)

# DRY_RUN: true なら Hyperliquid 注文を発行せずログのみ
DRY_RUN = os.environ.get("DRY_RUN", True).lower() in ("true", "1", "t")

# aws設定
AWS_ACCESS_KEY = os.environ.get("AWS_KEY", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET", "")
REGION_NAME = "ap-northeast-1"

# --- インフラ設定 ---
INFURA_RPC_URL = os.environ.get("INFURA_RPC_URL", "")
ALCHEMY_RPC_URL = os.environ.get("ALCHEMY_RPC_URL", "")
HL_BASE_URL = constants.MAINNET_API_URL

# ===================================================================
# V4 コントラクト設定 (デフォルトは Anvil デプロイ結果)
# ===================================================================


POOL_MANAGER_ADDRESS = os.environ.get(
    "POOL_MANAGER_ADDRESS", "0x13B92bc2397c97b90fc92bf42d64A832DbB66aD4"
)
LP_ROUTER_ADDRESS = os.environ.get(
    "LP_ROUTER_ADDRESS", "0xbc13E6a60C5E834E98cd9388a88E28E17354D8F8"
)

# Hook アドレス
HOOK_ADDRESS = os.environ.get(
    "HOOK_ADDRESS", "0xEbB6C7CAc8824970e7BA98d63e503267132Ac080"
)

# PoolKey を構成する要素
CURRENCY0 = os.environ.get("CURRENCY0", "0x511245A8701Db0512d907e0590f72a1Fd27C7d22")
CURRENCY1 = os.environ.get("CURRENCY1", "0xF46Af532e1E648E61690631AaAB9c1A60374A184")
WETH_ADDRESS = CURRENCY0
USDC_ADDRESS = CURRENCY1
POOL_FEE = 8388608  # DYNAMIC_FEE_FLAG
TICK_SPACING = 60

# StateLibrary 定数 (PoolManagerストレージレイアウト)
POOLS_SLOT = 6
LIQUIDITY_OFFSET = 3

# PoolManager ABI (extsload のみ)
POOL_MANAGER_ABI = json.dumps(
    [
        {
            "inputs": [{"internalType": "bytes32", "name": "slot", "type": "bytes32"}],
            "name": "extsload",
            "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
)

# Hook ABI (MarketVolatile イベント監視用)
HOOK_ABI = json.dumps(
    [
        {
            "anonymous": False,
            "inputs": [
                {
                    "indexed": True,
                    "internalType": "PoolId",
                    "name": "poolId",
                    "type": "bytes32",
                },
                {
                    "indexed": False,
                    "internalType": "uint256",
                    "name": "volatilityDiff",
                    "type": "uint256",
                },
                {
                    "indexed": False,
                    "internalType": "uint24",
                    "name": "appliedFee",
                    "type": "uint24",
                },
            ],
            "name": "MarketVolatile",
            "type": "event",
        },
        {
            "inputs": [{"internalType": "PoolId", "name": "poolId", "type": "bytes32"}],
            "name": "lastPrices",
            "outputs": [{"internalType": "uint160", "name": "", "type": "uint160"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "defaultFee",
            "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "highFee",
            "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
            "stateMutability": "view",
            "type": "function",
        },
    ]
)

DISCORD_URL = os.environ.get("DISCORD_URL")


# ===================================================================
# 共通ヘルパー (V3版と同一)
# ===================================================================
# --- Discord Embed用のカラーマップ ---
DISCORD_COLORS = {
    "success": 0x2ECC71,  # 緑
    "error": 0xE74C3C,  # 赤
    "warning": 0xF39C12,  # 黄
    "info": 0x3498DB,  # 青
    "default": 0x95A5A6,  # グレー
}


def _detect_color(message):
    """メッセージ内容から自動で色を判別"""
    if any(k in message for k in ("❌", "🛑", "FAILED", "Error")):
        return DISCORD_COLORS["error"]
    if any(k in message for k in ("🚨", "BAILOUT", "⚠")):
        return DISCORD_COLORS["warning"]
    if any(k in message for k in ("✅", "☁️", "🚀")):
        return DISCORD_COLORS["success"]
    return DISCORD_COLORS["info"]


def sendDiscord(message):
    """シンプルなEmbed形式でDiscordに通知"""
    try:
        embed = {
            "description": message,
            "color": _detect_color(message),
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        payload = {"embeds": [embed]}
        requests.post(DISCORD_URL, json=payload)
    except:
        pass


def sendDiscordReport(equity_data, liquidity):
    """DynamoDB保存時にリッチEmbed形式で詳細レポートを送信"""
    try:
        embed = {
            "title": "📊 Delta Neutral Bot Report",
            "color": DISCORD_COLORS["success"],
            "fields": [
                {
                    "name": "💰 Total Equity",
                    "value": f"${equity_data['total_equity']:.2f}",
                    "inline": True,
                },
                {
                    "name": "📈 ETH Price",
                    "value": f"${equity_data['eth_price']:.2f}",
                    "inline": True,
                },
                {
                    "name": "🏊 Pool Liquidity",
                    "value": f"{liquidity:,.0f}",
                    "inline": True,
                },
                {
                    "name": "🦄 Uniswap Value",
                    "value": f"${equity_data['uni_value']:.2f}",
                    "inline": True,
                },
                {
                    "name": "📊 HL Value",
                    "value": f"${equity_data['hl_value']:.2f}",
                    "inline": True,
                },
                {
                    "name": "💸 Funding Fees",
                    "value": f"${equity_data['funding_fees']:.4f}",
                    "inline": True,
                },
                {
                    "name": "📐 LP Delta",
                    "value": f"{equity_data.get('lp_delta', 0):.4f} ETH",
                    "inline": True,
                },
                {
                    "name": "🔄 Net Delta",
                    "value": f"{equity_data.get('net_delta', 0):.4f} ETH",
                    "inline": True,
                },
                {
                    "name": "📏 Raw Net Delta",
                    "value": f"{equity_data.get('raw_net_delta', 0):.4f} ETH",
                    "inline": True,
                },
                {
                    "name": "📊 Step PnL",
                    "value": f"${equity_data.get('step_pnl', 0):.4f}",
                    "inline": True,
                },
                {
                    "name": "📈 Cumulative PnL",
                    "value": f"${equity_data.get('cum_pnl', 0):.4f}",
                    "inline": True,
                },
            ],
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "footer": {"text": "Delta Neutral Bot V4"},
        }
        payload = {"embeds": [embed]}
        requests.post(DISCORD_URL, json=payload)
    except:
        pass


def get_price_from_sqrt(sqrt_pa):
    """sqrtPrice から実際のUSD価格を計算 (WETH/USDCペア用)"""
    price_raw = sqrt_pa**2
    decimal_shift = 10 ** (18 - 6)
    price_usd = price_raw * decimal_shift
    return price_usd


def convertPriceToTick(price):
    targetPrice = price / 1e12
    targetTick = int(math.log(targetPrice, 1.0001))
    return targetTick


def format_decimal(val, precision=18):
    """floatをDynamoDB用のDecimalに安全に変換する"""
    if val is None:
        return None
    try:
        f_val = float(val)
    except Exception:
        return Decimal(0)
    if f_val != f_val:
        return None
    if f_val == float("inf") or f_val == float("-inf"):
        return None
    if abs(f_val) < 1e-15:
        return Decimal("0")
    formatted_str = f"{f_val:.18f}"
    return Decimal(formatted_str).normalize()


def get_sqrt_from_price(price_usd):
    return math.sqrt(price_usd / (10**12))


# ===================================================================
# PnLトラッカ (V3版と同一)
# ===================================================================
class DeltaPnLTracker:
    def __init__(self):
        self.cumulative_pnl = 0.0
        self.last_price = None
        self.last_net_delta = 0.0

    def update(self, current_price, current_net_delta):
        if self.last_price is None:
            self.last_price = current_price
            self.last_net_delta = current_net_delta
            return 0.0, 0.0
        price_change = current_price - self.last_price
        step_pnl = self.last_net_delta * price_change
        self.cumulative_pnl += step_pnl
        self.last_price = current_price
        self.last_net_delta = current_net_delta
        return step_pnl, self.cumulative_pnl


# ===================================================================
# メイン Bot クラス (V4対応)
# ===================================================================
class DeltaNeutralBotV4:
    def __init__(self):
        # 1. 接続初期化
        # メインループ用のweb3インスタンス
        self.w3 = Web3(Web3.HTTPProvider(INFURA_RPC_URL))
        self.coin = "ETH"

        self.secret = ARB_SECRET

        # V4コントラクト: extsload経由で読み取り
        self.pool_manager = self.w3.eth.contract(
            address=Web3.to_checksum_address(POOL_MANAGER_ADDRESS),
            abi=json.loads(POOL_MANAGER_ABI),
        )

        # フック監視用のweb3インスタンス
        self.w3_hook = Web3(Web3.HTTPProvider(ALCHEMY_RPC_URL))

        # Hook コントラクト (イベント監視用)
        if HOOK_ADDRESS:
            self.hook_contract = self.w3_hook.eth.contract(
                address=Web3.to_checksum_address(HOOK_ADDRESS),
                abi=json.loads(HOOK_ABI),
            )
        else:
            self.hook_contract = None

        # poolId計算ユーティリティ
        self.pu = poolUtils(POOLS_SLOT, LIQUIDITY_OFFSET)

        # PoolId を計算
        self.pool_id = self.pu.compute_pool_id(
            Web3.to_checksum_address(CURRENCY0),
            Web3.to_checksum_address(CURRENCY1),
            POOL_FEE,
            TICK_SPACING,
            Web3.to_checksum_address(HOOK_ADDRESS),
        )
        log.info(f"📋 Pool ID: {self.pool_id.hex()}")

        # Hyperliquid
        self.account = eth_account.Account.from_key(HL_PRIVATE_KEY)
        self.exchange = Exchange(
            self.account, HL_BASE_URL, account_address=MAIN_ACCOUNT_ADDRESS
        )
        self.info = Info(HL_BASE_URL, skip_ws=True)

        # DynamoDB
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=REGION_NAME,
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
        )
        self.table = self.dynamodb.Table("v4Hook_DeltaNeut.")

        # ポジション情報 (V4ではticksはプール作成時/リバランス時に設定)
        self.tickLower = int(os.environ.get("TICK_LOWER", "-60000"))
        self.tickUpper = int(os.environ.get("TICK_UPPER", "60000"))
        self.position_salt = Web3.to_bytes(hexstr=os.environ.get("POSITION_SALT"))

        # 時間フィルタ
        self.firstBreachTime = None
        self.BailoutBreachTime = None
        self.cooltime = 0.0

        # --- 同期制御 (Hook vs デルタリバランス) ---
        self.trade_lock = threading.Lock()
        self.hook_triggered = False  # Hook側がトレード実行済みフラグ
        self.last_processed_block = 0  # 重複イベント防止

        # --- DRY_RUN: 仮想ヘッジポジション ---
        self.virtual_hedge_pos = 0.0  # 仮想ショートポジション (ETH)

        log.info(f"✅ V4 Bot initialized | RPC: {INFURA_RPC_URL}")
        log.info(f"📍 PoolManager: {POOL_MANAGER_ADDRESS}")
        log.info(f"📍 Hook: {HOOK_ADDRESS}")
        log.info(f"🎨 DRY_RUN: {DRY_RUN}")

    # CEX価格取得用ヘルパ関数
    def get_cex_price(self, price):
        try:
            # Hyperliquidから全銘柄の現在価格(Mark Price)を取得
            mids = self.info.all_mids()
            cex_price = float(mids["ETH"])
        except Exception as e:
            # 万が一APIエラーが起きた場合は、AMMの価格を代用する（安全装置）
            cex_price = price
            log.info(f"HL apiError: {e}")
        return cex_price

    ##################################################################
    # デバッグ用関数
    def hltest(self):
        # 1. 現物（Spot）APIから、純粋なUSDC残高（現金）を取得
        spot_state = self.info.spot_user_state(MAIN_ACCOUNT_ADDRESS)
        spot_usdc = 0.0
        for balance in spot_state.get("balances", []):
            if balance["coin"] == "USDC":
                spot_usdc = float(balance["total"])
                break

        # 2. 先物（Perp）APIから、現在のポジションの「含み損益（Unrealized PnL）」の合計を計算
        user_state = self.info.user_state(MAIN_ACCOUNT_ADDRESS)
        unrealized_pnl = 0.0
        for position in user_state.get("assetPositions", []):
            pos_data = position.get("position", {})
            unrealized_pnl += float(pos_data.get("unrealizedPnl", 0.0))

        # 3. 現金残高に含み損益を足して、真の評価額とする
        hl_value_usd = spot_usdc + unrealized_pnl

        # 🔍 デバッグ用ログ出力
        log.info(
            f"💰 HL Total: {hl_value_usd} (Cash: {spot_usdc}, PnL: {unrealized_pnl})"
        )

        return hl_value_usd

    # ============================================================
    # V4: オンチェーンデータ取得
    # ============================================================
    def get_onchain_data(self):
        """
        V4 プールの価格、ポジションの流動性(L)、ヘッジポジションを一括取得。
        V3版の get_onchain_data() に相当。

        V3: Pool.slot0() + NFPM.positions(tokenId)
        V4: StateView.getSlot0(poolId) + StateView.getPositionInfo(poolId, owner, tL, tU, salt)
        """
        try:
            # --- A. V4 プール現在価格 (extsload経由) ---
            sqrtPriceX96, tick = self.pu.read_slot0_via_extsload(
                self.pool_manager, self.pool_id
            )

            # Hookコントラクトから直接 defaultFee を取得する
            if self.hook_contract is not None:
                try:
                    lp_fee_raw = self.hook_contract.functions.defaultFee().call()
                    current_fee_pct = lp_fee_raw / 10000.0
                except Exception as e:
                    log.debug(f"Fee fetch error: {e}")
                    current_fee_pct = 0.05
            else:
                current_fee_pct = 0.05
            Q96 = 2**96
            sqrtP = sqrtPriceX96 / Q96
            human_price = (sqrtP**2) * 1e12  # WETH(18)-USDC(6) のデシマル補正

            # --- B. V4 プール流動性 (extsload経由) ---
            liquidity = self.pu.read_liquidity_via_extsload(
                self.pool_manager, self.pool_id
            )
            real_L = float(liquidity)

            # --- C. ヘッジポジション ---
            if DRY_RUN:
                # DRY_RUN: 仮想ポジションを使用
                current_hedge = self.virtual_hedge_pos
                print("DRY RUN right now")
            else:
                # 本番: Hyperliquid から取得
                user_state = self.info.user_state(MAIN_ACCOUNT_ADDRESS)

                # # 💡 追加：APIが返してきた生のデータをログに出力！
                # log.info(f"🔍 DEBUG HL API: {user_state}")

                current_hedge = 0.0
                for pos in user_state["assetPositions"]:
                    if pos["position"]["coin"] == "ETH":
                        current_hedge = float(pos["position"]["szi"])
                        break

            self.L = real_L
            self.hedge_pos = current_hedge

            pos_owner = Web3.to_checksum_address(LP_ROUTER_ADDRESS)
            my_liquidity = self.pu.get_position_liquidity_via_extsload(
                self.pool_manager,
                self.pool_id,
                pos_owner,
                self.tickLower,
                self.tickUpper,
                self.position_salt,
            )

            return {
                "sqrtP_raw": sqrtP,
                "price": human_price,
                "tick": tick,
                "L": real_L,
                "my_L": my_liquidity,
                "tickLower": self.tickLower,
                "tickUpper": self.tickUpper,
                "hedge_pos": current_hedge,
                "fee_pct": current_fee_pct,
            }

        except Exception as e:
            log.info(f"Data Fetch Error: {e}")
            return None

    # ============================================================
    # トークン量計算 (V3/V4共通のAMM数学)
    # ============================================================
    def get_token_amounts(self, liquidity, sqrtP, tick_lower, tick_upper):
        """流動性Lと価格から、現在のETHとUSDCの保有量を計算する"""
        sqrtPa = 1.0001 ** (tick_lower / 2)
        sqrtPb = 1.0001 ** (tick_upper / 2)

        amount0 = 0.0  # ETH
        amount1 = 0.0  # USDC

        if sqrtP < sqrtPa:
            amount0 = liquidity * (1 / sqrtPa - 1 / sqrtPb)
            amount1 = 0.0
        elif sqrtP >= sqrtPb:
            amount0 = 0.0
            amount1 = liquidity * (sqrtPb - sqrtPa)
        else:
            amount0 = liquidity * (1 / sqrtP - 1 / sqrtPb)
            amount1 = liquidity * (sqrtP - sqrtPa)

        return amount0 / 1e18, amount1 / 1e6

    # ============================================================
    # 閾値計算
    # ============================================================
    def calcThreshold(self, total_equity, currentPrice):
        allowedRiskUSD = total_equity * ALLOWABLE_RISK_PCT
        if allowedRiskUSD < 15:
            allowedRiskUSD = 15
        thresholdETH = allowedRiskUSD / currentPrice
        return thresholdETH

    # ============================================================
    # Funding Fee計算
    # ============================================================
    def calculate_uncollected_fees(
        self,
        current_tick,
        tick_lower,
        tick_upper,
        liquidity,
        fg_global,
        fg_outside_lower,
        fg_outside_upper,
        fg_inside_last,
    ):
        """未回収のToken0, Token1の手数料を計算して返す"""
        Q256 = 2**256

        uncollected = [0, 0]
        for i in range(2):
            global_fee = fg_global[i]
            outside_lower = fg_outside_lower[i]
            outside_upper = fg_outside_upper[i]
            inside_last = fg_inside_last[i]

            # 下限Tickより下の計算
            if current_tick >= tick_lower:
                fee_below = outside_lower
            else:
                fee_below = (global_fee - outside_lower) % Q256

            # 上限Tickより上の計算
            if current_tick < tick_upper:
                fee_above = outside_upper
            else:
                fee_above = (global_fee - outside_upper) % Q256

            # 現在のレンジ内の手数料を算出
            fee_inside = (global_fee - fee_below - fee_above) % Q256

            # 未回収分 = 流動性量 × (現在値 - 前回更新時の値) / 2^128
            earned = (liquidity * ((fee_inside - inside_last) % Q256)) // (2**128)
            uncollected[i] = earned

        return uncollected[0], uncollected[1]

    # ============================================================
    # 総資産計算
    # ============================================================
    def get_total_equity(self):
        """UniswapとHyperliquidの合計資産価値(USD)を計算"""
        try:
            data = self.get_onchain_data()
            if data is None or data["L"] == 0:
                return None

            cex_price = self.get_cex_price(data["price"])

            eth_amount, usdc_amount = self.get_token_amounts(
                data["L"], data["sqrtP_raw"], data["tickLower"], data["tickUpper"]
            )

            # V4では手数料回収はPositionManager経由
            # テスト段階では未回収手数料を0として計算
            pos_owner = Web3.to_checksum_address(LP_ROUTER_ADDRESS)

            # 1. 各種手数料データをスロットから取得
            fg_global = self.pu.read_fee_globals_via_extsload(
                self.pool_manager, self.pool_id
            )
            fg_out_lower = self.pu.get_tick_fee_outside_via_extsload(
                self.pool_manager, self.pool_id, data["tickLower"]
            )
            fg_out_upper = self.pu.get_tick_fee_outside_via_extsload(
                self.pool_manager, self.pool_id, data["tickUpper"]
            )
            fg_in_last = self.pu.get_position_fee_inside_last_via_extsload(
                self.pool_manager,
                self.pool_id,
                pos_owner,
                data["tickLower"],
                data["tickUpper"],
                self.position_salt,
            )

            # 2. 未回収手数料(Wei単位)の計算
            current_tick = self.pu.read_slot0_via_extsload(
                self.pool_manager, self.pool_id
            )[1]
            uncollected_wei0, uncollected_wei1 = self.calculate_uncollected_fees(
                current_tick,
                data["tickLower"],
                data["tickUpper"],
                int(data["L"]),
                fg_global,
                fg_out_lower,
                fg_out_upper,
                fg_in_last,
            )

            log.info(
                f"🔎 DEBUG FEES (Wei): WETH={uncollected_wei0}, USDC={uncollected_wei1}"
            )

            # 3. Weiから人間に読める枚数に変換 (WETH=18桁, USDC=6桁)
            fees_eth = uncollected_wei0 / 1e18
            fees_usdc = uncollected_wei1 / 1e6
            # -----------------------------------

            uni_value_usd = (eth_amount + fees_eth) * cex_price + (
                usdc_amount + fees_usdc
            )
            funding_fees = fees_eth * cex_price + fees_usdc

            if DRY_RUN:
                # DRY_RUN: 仮想ポジションの含み損益を簡易計算
                hl_value_usd = 0.0  # 仮想ポジションの証拠金は追跡しない
            else:
                spot_state = self.info.spot_user_state(MAIN_ACCOUNT_ADDRESS)
                spot_usdc = 0.0
                for balance in spot_state.get("balances", []):
                    if balance["coin"] == "USDC":
                        spot_usdc = float(balance["total"])
                        break

                # 2. 先物（Perp）APIから、現在のポジションの「含み損益（Unrealized PnL）」の合計を計算
                user_state = self.info.user_state(MAIN_ACCOUNT_ADDRESS)
                unrealized_pnl = 0.0
                for position in user_state.get("assetPositions", []):
                    pos_data = position.get("position", {})
                    unrealized_pnl += float(pos_data.get("unrealizedPnl", 0.0))

                # 3. 現金残高に含み損益を足して、真の評価額とする
                hl_value_usd = spot_usdc + unrealized_pnl

                # 🔍 デバッグ用ログ出力
                log.info(
                    f"💰 HL Total: {hl_value_usd} (Cash: {spot_usdc}, PnL: {unrealized_pnl})"
                )
            # ERC20の残高を取得するための最小限のABI
            ERC20_ABI = [
                {
                    "constant": True,
                    "inputs": [{"name": "_owner", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "balance", "type": "uint256"}],
                    "type": "function",
                }
            ]

            try:
                # ① 生のETH残高 (ガス代用など / 18 decimals)
                eth_wei = self.w3.eth.get_balance(MAIN_ACCOUNT_ADDRESS)
                eth_wallet = eth_wei / (10**18)

                # ② WETH残高 (18 decimals)
                weth_contract = self.w3.eth.contract(
                    address=WETH_ADDRESS, abi=ERC20_ABI
                )
                weth_wei = weth_contract.functions.balanceOf(
                    MAIN_ACCOUNT_ADDRESS
                ).call()
                weth_wallet = weth_wei / (10**18)

                # ③ USDC残高 (ArbitrumネイティブUSDCは 6 decimals)
                usdc_contract = self.w3.eth.contract(
                    address=USDC_ADDRESS, abi=ERC20_ABI
                )
                usdc_mwei = usdc_contract.functions.balanceOf(
                    MAIN_ACCOUNT_ADDRESS
                ).call()
                usdc_wallet = usdc_mwei / (10**6)

                # ウォレット内の総資産をUSD換算（ETHとWETHはCEX価格を掛ける）
                wallet_value_usd = (eth_wallet + weth_wallet) * cex_price + usdc_wallet

            except Exception as e:
                log.error(f"ウォレット残高の取得に失敗しました: {e}")
                wallet_value_usd = 0.0

            # ==========================================
            # 4. 最終的な総資産（Total Equity）の合算
            # ==========================================
            total_equity = uni_value_usd + hl_value_usd + wallet_value_usd

            # デバッグ用ログ（3つの内訳を出力）
            log.info(
                f"💵 Total Equity: {total_equity:.2f} (Pool: {uni_value_usd:.2f}, HL: {hl_value_usd:.2f}, Wallet: {wallet_value_usd:.2f})"
            )
            self.ETHthreshold = self.calcThreshold(
                total_equity=total_equity, currentPrice=cex_price
            )

            return {
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "uni_value": uni_value_usd,
                "hl_value": hl_value_usd,
                "funding_fees": funding_fees,
                "total_equity": total_equity,
                "eth_price": data["price"],
            }

        except Exception as e:
            log.info(f"Equity Calc Error: {e}")
            return None

    # ============================================================
    # デルタ計算
    # ============================================================
    def calcRawDelta(self, currentPrice):
        DECIMALS_ETH = 1e18
        raw_amount0_wei = 0.0

        sp = get_sqrt_from_price(currentPrice)
        sqrtPa = 1.0001 ** (self.tickLower / 2)
        sqrtPb = 1.0001 ** (self.tickUpper / 2)
        L = self.L
        hedge_pos = self.hedge_pos

        if sp < sqrtPa:
            raw_amount0_wei = L * (1 / sqrtPa - 1 / sqrtPb)
        elif sp >= sqrtPb:
            raw_amount0_wei = 0.0
        else:
            raw_amount0_wei = L * (1 / sp - 1 / sqrtPb)

        raw_net_delta = raw_amount0_wei / DECIMALS_ETH + hedge_pos
        return raw_net_delta

    # ============================================================
    # HL発注 (DRY_RUN対応)
    # ============================================================
    def execute_trade(self, amount_eth):
        """HLへ発注。DRY_RUN=true の場合はログのみ出力。"""
        is_buy = amount_eth > 0
        sz = round(abs(amount_eth), 4)

        MAX_TRADE_SIZE = 2.0

        if sz == 0:
            return

        if sz > MAX_TRADE_SIZE:
            log.info(
                f"\n🛑 危険: 発注サイズ({sz} ETH)が上限({MAX_TRADE_SIZE} ETH)を超えています！"
            )
            log.info("計算ロジックを確認してください。Botを停止します。")
            sendDiscord("計算ロジックを確認してください。Botを停止します。")
            exit()

        direction = "BUY" if is_buy else "SELL"
        log.info(f"🚀 ORDER: {direction} {sz} ETH")
        orderLog.info(f"🚀 ORDER: {direction} {sz} ETH")
        sendDiscord(f"🚀 ORDER: {direction} {sz} ETH")

        # --- DRY_RUN モード: 仮想ポジション更新 + ログ ---
        if DRY_RUN:
            self.virtual_hedge_pos += amount_eth  # 仮想ポジションを増減
            log.info(
                f"🎨 [DRY_RUN] 仮想注文: {direction} {sz} ETH | "
                f"仮想ポジション: {self.virtual_hedge_pos:+.4f} ETH"
            )
            orderLog.info(
                f"🎨 [DRY_RUN] {direction} {sz} ETH | "
                f"Virtual Pos: {self.virtual_hedge_pos:+.4f} ETH"
            )
            sendDiscord(
                f"🎨 [DRY_RUN] {direction} {sz} ETH | "
                f"仮想Pos: {self.virtual_hedge_pos:+.4f} ETH"
            )
            return "DRY_RUN"

        try:
            market_result = self.exchange.market_open(self.coin, is_buy, sz=sz)
            log.info(f"✅ 成行注文完了: {market_result['status']}")
            sendDiscord(f"✅ 成行注文完了: {market_result['status']}")
            return "MAKER_FILLED"
        except Exception as e:
            log.info(f"❌ 成行注文も失敗しました (致命的エラー): {e}")
            sendDiscord(f"❌ 成行注文も失敗しました (致命的エラー): {e}")
            return "FAILED"

    # ============================================================
    # DynamoDB記録
    # ============================================================
    def save_to_dynamodb(self, equity_data, liquidity=0):
        """資産データをDynamoDBに送信し、リッチEmbedでDiscordに通知"""
        try:
            item = {
                "timestamp": equity_data["timestamp"],
                "uni_value": format_decimal(equity_data["uni_value"]),
                "hl_value": format_decimal(equity_data["hl_value"]),
                "funding_fees": format_decimal(equity_data["funding_fees"]),
                "step_pnl": format_decimal(equity_data["step_pnl"]),
                "cum_pnl": format_decimal(equity_data["cum_pnl"]),
                "total_equity": format_decimal(equity_data["total_equity"]),
                "eth_price": format_decimal(equity_data["eth_price"]),
                "lp_delta": format_decimal(equity_data.get("lp_delta", 0)),
                "net_delta": format_decimal(equity_data.get("net_delta", 0)),
                "raw_net_delta": format_decimal(equity_data.get("raw_net_delta", 0)),
                "cex_price": format_decimal(equity_data.get("cex_price", 0)),
            }
            self.table.put_item(Item=item)
            log.info(f"☁️ Saved to DynamoDB: ${equity_data['total_equity']:.2f}")
            sendDiscordReport(equity_data, liquidity)
        except Exception as e:
            log.info(f"❌ DynamoDB Error: {e}")
            sendDiscord(f"❌ DynamoDB Error: {e}")

    # ============================================================
    # Hook イベント監視スレッド (別スレッドで3秒間隔ポーリング)
    # ============================================================
    def _hook_event_loop(self):
        """
        MarketVolatile イベントを3秒間隔でポーリング。
        検知時は trade_lock を取得し、デルタを即座に0にするヘッジ注文を発行。
        通常のデルタリバランスと排他制御される。
        """
        if self.hook_contract is None:
            log.info("🪝 Hook contract not configured, event loop disabled")
            return

        log.info("🪝 Hook event monitoring thread started (3s interval)")

        while True:
            try:
                current_block = self.w3_hook.eth.block_number

                # 新しいブロックがなければスキップ
                if current_block <= self.last_processed_block:
                    time.sleep(15)
                    continue

                # from_block: 前回処理済み+1、to_block: 最新
                from_block = (
                    self.last_processed_block + 1
                    if self.last_processed_block > 0
                    else max(0, current_block - 5)
                )
                events = self.hook_contract.events.MarketVolatile.get_logs(
                    from_block=from_block,
                    to_block=current_block,
                )

                for event in events:
                    args = event["args"]
                    fee_raw = args["appliedFee"]
                    fee_percent = (fee_raw & 0xFFFFF) / 10000.0
                    diff = args["volatilityDiff"]
                    reason = "WHALE IMPACT" if diff == 0 else "PRICE VOLATILITY"

                    log.info(
                        f"🚨 Hook Alert: {reason} | Fee: {fee_percent:.2f}% | Block: {event['blockNumber']}"
                    )
                    sendDiscord(f"🚨 Hook Alert: {reason} | Fee: {fee_percent:.2f}%")

                    # --- trade_lock を取得してデルタ0化 ---
                    with self.trade_lock:
                        self._execute_hook_delta_zero(reason)

                self.last_processed_block = current_block

            except Exception as e:
                log.debug(f"Hook event loop error: {e}")

            time.sleep(15)

    def _execute_hook_delta_zero(self, reason):
        """
        Hookイベント検知時にデルタを0にするヘッジ注文を発行。
        trade_lock を保持した状態で呼ばれる前提。
        """
        try:
            data = self.get_onchain_data()
            if data is None or data["L"] == 0:
                log.info("🪝 Hook delta-zero skipped: no data or zero liquidity")
                return

            cex_price = self.get_cex_price(data["price"])

            raw_net_delta = self.calcRawDelta(cex_price)

            if abs(raw_net_delta) < 0.001:  # 既にほぼゼロなら不要
                log.info(
                    f"🪝 Hook: delta already near zero ({raw_net_delta:.4f}), no trade needed"
                )
                return

            log.info(
                f"🪝 HOOK TRADE: {reason} detected! "
                f"Raw Delta: {raw_net_delta:.4f} → zeroing out"
            )
            orderLog.info(f"🪝 HOOK TRADE: {reason} | Delta: {raw_net_delta:.4f} → 0")
            sendDiscord(
                f"🪝 HOOK TRADE: {reason} | Delta: {raw_net_delta:.4f} → 0 注文発行"
            )

            self.execute_trade(-1 * raw_net_delta)
            self.hook_triggered = True  # メインループのリバランスをスキップさせる
            self.cooltime = time.time()

        except Exception as e:
            log.info(f"❌ Hook delta-zero error: {e}")
            sendDiscord(f"❌ Hook delta-zero error: {e}")

    def _executeReposition(self, data, pr, inIsLiquidityZero):
        """_summary_
            リポジション実行
        Args:
            data (dataFrame): get_onchain_dataの返り値data
            pr (PoolRepositioner): PoolRepositionerクラスインスタンス
        """
        currentCexPrice = self.get_cex_price(data["price"])
        log.info(f"Try repositioning @price:${currentCexPrice}...")
        PRresponse = pr.executeReposition(
            INFURA_RPC_URL, currentCexPrice, inIsLiquidityZero
        )
        if PRresponse:
            log.info(f"successfully repositioned @${currentCexPrice}!")
            sendDiscord(f"successfully repositioned @${currentCexPrice}!")
            return True
        else:
            return False

    # ============================================================
    # メインループ
    # ============================================================
    def run(self):
        log.info("🛡️ V4 Delta Neutral Bot Started. Waiting for liquidity...")

        # Hook イベント監視スレッド起動 (daemon=True でメイン終了時に自動停止)
        hook_thread = threading.Thread(target=self._hook_event_loop, daemon=True)
        hook_thread.start()
        last_log_time = datetime.datetime.now()
        DECIMALS_ETH = 1e18

        dataInit = self.get_onchain_data()

        sqrtPa = 1.0001 ** (dataInit["tickLower"] / 2)
        sqrtPb = 1.0001 ** (dataInit["tickUpper"] / 2)
        # レンジアウトスコアクラスのインスタンス生成
        oor = oorDetector(
            upperPrice=get_price_from_sqrt(sqrtPb),
            lowerPrice=get_price_from_sqrt(sqrtPa),
            thresholdScore=float(os.environ.get("THRESHOLD_SCORE")),
            k=float(os.environ.get("K")),
        )

        # プールのリポジションクラスのインスタンス生成
        pr = PoolRepositioner(
            self.pool_manager,
            HOOK_ADDRESS,
            dataInit["my_L"],
            dataInit["tickLower"],
            dataInit["tickUpper"],
            ARB_SECRET,
        )

        log.info("PoolRepositioner SET")
        log.info(
            f"Liquidity:{dataInit['my_L']}, tickLower:{dataInit['tickLower']}, tickUpper:{dataInit['tickUpper']}"
        )
        # リポジションの試行回数
        repositionCount = 0

        lpf = LowPassFilter(alpha=0.15)
        tracker = DeltaPnLTracker()

        while True:
            data = self.get_onchain_data()

            if data is None:
                time.sleep(3)
                continue

            # cexPrice取得
            cex_price = self.get_cex_price(data["price"])

            # --- 1. ガード条件: 流動性が0ならポジション作成---
            if data["my_L"] == 0:
                log.info("pool Liquidity is zero. Making a new Pool position...")
                if self._executeReposition(data, pr, isLiquidityZero.YES.value):
                    repositionCount = 0
                    time.sleep(5)
                    continue
                elif repositionCount < 4:
                    log.info("FAILED to create PoolLIquidity. Trying again...")
                    repositionCount += 1
                    time.sleep(5)
                    continue
                else:
                    log.info("FAILED to create Position 3 times. Stopping Bot...")
                    exit()

            if pr.liquidity != data["my_L"]:
                log.info(
                    f"Liquidity change detected, changing L = {pr.liquidity} to {data['my_L']}"
                )
                pr.liquidity = data["my_L"]

            # --- 2. デルタ計算 ---

            sqrtPa = 1.0001 ** (data["tickLower"] / 2)
            sqrtPb = 1.0001 ** (data["tickUpper"] / 2)
            sp_cex = get_sqrt_from_price(cex_price)
            L = data["L"]

            smoothedSP = lpf.update(sp_cex)

            amount0_wei = 0.0
            if sp_cex < sqrtPa:
                amount0_wei = L * (1 / sqrtPa - 1 / sqrtPb)
                raw_amount0_wei = amount0_wei
            elif sp_cex >= sqrtPb:
                amount0_wei = 0.0
                raw_amount0_wei = amount0_wei
            else:
                amount0_wei = L * (1 / smoothedSP - 1 / sqrtPb)
                raw_amount0_wei = L * (1 / sp_cex - 1 / sqrtPb)

            lp_delta_eth = amount0_wei / DECIMALS_ETH
            raw_lp_delta_eth = raw_amount0_wei / DECIMALS_ETH

            net_delta = lp_delta_eth + data["hedge_pos"]
            raw_net_delta = raw_lp_delta_eth + data["hedge_pos"]

            current_price = data["price"]
            lp_value_usd = lp_delta_eth * current_price

            log.info(
                f"\r AMMPrice:${current_price:.1f} | CEXPrice:${cex_price:.1f} |CurrentThreshold:{self.ETHthreshold:.3f} | "
                f"Fee:{data['fee_pct']:.2f} | "
                f"LP:{lp_delta_eth:.3f}ETH (${lp_value_usd:.0f}) | "
                f"Hedge:{data['hedge_pos']:.3f} | Net:{net_delta:.4f} \n"
                f"💡 My Exact Liquidity (For Withdraw): {data['my_L']}"
                f" Total Liquidity: {data['L']}"
            )

            # --- 3. リバランス判定 (trade_lock で排他制御) ---
            with self.trade_lock:
                # Hook側がトレード実行済みの場合、リバランスをスキップ
                if self.hook_triggered:
                    log.info(
                        "🪝 Hook trade was executed, skipping this rebalance cycle"
                    )
                    self.hook_triggered = False
                    self.firstBreachTime = None
                    self.BailoutBreachTime = None
                    time.sleep(20)
                    continue

                hasAlreadyTraded = False
                currentTime = time.time()

                if abs(net_delta) > self.ETHthreshold:
                    elapsedTime = currentTime - self.cooltime

                    # if self.firstBreachTime is None:
                    #     self.firstBreachTime = datetime.datetime.now()
                    # elif (datetime.datetime.now() - self.firstBreachTime).seconds > 5:
                    if elapsedTime <= 180:
                        log.info("\n has traded in 3 minutes. Cooldowning...")
                        sendDiscord("\n has traded in 3 minutes. Cooldowning...")
                    else:
                        log.info(
                            f"\n🚨 Rebalance Required! Net Delta: {net_delta:.4f}, "
                            f"Current Price: {current_price:.2f}"
                        )
                        orderLog.info(
                            f"\n🚨 Rebalance Required! Net Delta: {net_delta:.4f}, "
                            f"Current Price: {current_price:.2f}"
                        )
                        sendDiscord(
                            f"\n🚨 Rebalance Required! Net Delta: {net_delta:.4f}, "
                            f"Current Price: {current_price:.2f}"
                        )
                        self.execute_trade(-1 * net_delta)
                        lpf.smoothed_value = None
                        hasAlreadyTraded = True
                        self.cooltime = currentTime
                # else:
                #     self.firstBreachTime = None

                # --- 4. 緊急脱出処理 ---
                if (
                    abs(raw_net_delta) < 3.3 * self.ETHthreshold
                    and abs(raw_net_delta) > 1.5 * self.ETHthreshold
                    and (not hasAlreadyTraded)
                ):
                    if self.BailoutBreachTime is None:
                        self.BailoutBreachTime = datetime.datetime.now()
                    elif (datetime.datetime.now() - self.BailoutBreachTime).seconds > 2:
                        log.info(
                            f"\n🚨BAILOUT!! Raw Net Delta: {raw_net_delta:.4f}, "
                            f"Current Price: {current_price:.2f}"
                        )
                        orderLog.info(
                            f"\n🚨BAILOUT!! Raw Net Delta: {raw_net_delta:.4f}, "
                            f"Current Price: {current_price:.2f}"
                        )
                        sendDiscord(
                            f"\n🚨BAILOUT!! Raw Net Delta: {raw_net_delta:.4f}, "
                            f"Current Price: {current_price:.2f}"
                        )

                        self.execute_trade(-1 * raw_net_delta)
                        self.cooltime = currentTime
                        lpf.smoothed_value = None
                else:
                    self.BailoutBreachTime = None

            # oorDetectorによるレンジアウト判定
            if oor.runDetector(cex_price):
                # プールのりポジションを行う
                if self._executeReposition(data, pr, isLiquidityZero.NO.value):
                    repositionCount = 0
                    time.sleep(5)
                    continue
                elif repositionCount < 3:
                    log.info("FAILED to create PoolLIquidity. Trying again...")
                    repositionCount += 1
                    time.sleep(5)
                    continue
                else:
                    log.info("FAILED to create Position 3 times. Stopping Bot...")
                    exit()

                time.sleep(10)
                continue

            # --- 6. DynamoDB記録 ---
            now = datetime.datetime.now()
            if (now - last_log_time).total_seconds() > RECORD_TIME:
                equity = self.get_total_equity()
                if equity:
                    step_pnl, cum_pnl = tracker.update(cex_price, raw_net_delta)
                    equity["lp_delta"] = lp_delta_eth
                    equity["net_delta"] = net_delta
                    equity["raw_net_delta"] = raw_net_delta
                    equity["step_pnl"] = step_pnl
                    equity["cum_pnl"] = cum_pnl
                    equity["cex_price"] = cex_price
                    self.save_to_dynamodb(equity, data["L"])
                    last_log_time = now

            time.sleep(30)


if __name__ == "__main__":
    bot = DeltaNeutralBotV4()
    bot.ETHthreshold = THRESHOLD
    log.info(f"⚙️ Config: Threshold={THRESHOLD}, DRY_RUN={DRY_RUN}")
    bot.run()
