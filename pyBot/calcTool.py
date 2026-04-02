import math


def calculate_v4_params(price_usdc, range_percent=0.015, tick_spacing=60):
    """
    指定したUSDC価格とレンジ幅から、V4用のTickとSqrtPriceX96を計算する
    (前提: Token0 = WETH(18 decimals), Token1 = USDC(6 decimals))
    """
    # 1. 実際の価格をWeiベース(raw_price)に変換
    # 1985 * (10^6) / (10^18)
    raw_price = price_usdc * (10**6) / (10**18)

    # 2. 中心となる現在のTickを計算 (底が1.0001の対数)
    current_tick = int(math.log(raw_price, 1.0001))

    # 3. 指定した%幅(例: 1.5% = 0.015)からTickのズレ幅を計算
    # 価格が (1 + 0.015) 倍になるためのTick数を逆算
    tick_delta = int(math.log(1 + range_percent, 1.0001))

    # 4. 上下限のTickを計算
    raw_tick_lower = current_tick - tick_delta
    raw_tick_upper = current_tick + tick_delta

    # 5. TickSpacing(60) でアライメント(切り捨て丸め)
    tick_lower = (raw_tick_lower // tick_spacing) * tick_spacing
    tick_upper = (raw_tick_upper // tick_spacing) * tick_spacing

    # 6. 中心価格の sqrtPriceX96 もついでに計算
    sqrt_price_x96 = int(math.sqrt(raw_price) * (2**96))

    return current_tick, tick_lower, tick_upper, sqrt_price_x96


# ==========================================
# 👇 ここにテストしたい価格とレンジ幅を入力
# ==========================================
print("Enter the target price(USDC):")
TARGET_PRICE = float(input())  # 現在のETH価格 (USDC)
RANGE_PCT = 0.015  # レンジ幅 (0.015 = ±1.5%)

current, lower, upper, sqrt_p = calculate_v4_params(TARGET_PRICE, RANGE_PCT)

print("=== V4 Parameter Calculator ===")
print(f"🎯 Target Price : {TARGET_PRICE} USDC")
print(f"📏 Range Width  : ±{RANGE_PCT * 100}%")
print("📐 TickSpacing  : 60")
print("-------------------------------")
print(f"🔍 Current Tick : {current}")
print(f"⬇️ Tick Lower   : {lower}")
print(f"⬆️ Tick Upper   : {upper}")
print(f"💎 sqrtPriceX96 : {sqrt_p}")
print("===============================")
