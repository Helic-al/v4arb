import datetime
import math
import os
import subprocess
from enum import Enum

from logger import setup_logger


class isLiquidityZero(Enum):
    NO = 0
    YES = 1


class PoolRepositioner:
    def __init__(
        self,
        inManager,
        inHookAddress,
        inLiquidity,
        inTickLower,
        inTickUpper,
        inPrivateKey,
    ):
        """
        inManager: プールマネージャアドレス
        inHookAddress: フックアドレス
        inRangeWidth: レンジ幅、入力した割合が上下レンジに設定される
        """
        self.manager = inManager
        self.hookAddress = inHookAddress
        self.liquidity = inLiquidity
        self.tickLower = inTickLower
        self.tickUpper = inTickUpper
        self.privateKey = inPrivateKey
        self.log = setup_logger("PoolReposition.log")

    def commandExecuter(self, inCommand, inEnv_vars):
        """
        inCommandを実行する関数
        return (boolean, cmdResult)
        """

        try:
            subprocess.run(["forge", "clean"], capture_output=True)

            result = subprocess.run(
                inCommand,
                cwd="../solidityHook",
                capture_output=True,
                text=True,
                check=True,
                env=inEnv_vars,
            )

            # ==========================================
            # 💡 ここを追加：実行結果をテキストファイルに追記(Append)保存
            # ==========================================
            with open("reposition_history.log", "a", encoding="utf-8") as f:
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"=== Reposition Executed: {now} ===\n")
                f.write(result.stdout)
                f.write("\n\n")

            self.log.info("✅ Reposition Success! Log saved to reposition_history.log")
            return (True, result.stdout)

        except subprocess.CalledProcessError as e:
            with open("reposition_history.log", "a", encoding="utf-8") as f:
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"=== ❌ ERROR: {now} ===\n")
                f.write("--- 📜 STDOUT (console.logなどの詳細トレース) ---\n")
                f.write(e.stdout if e.stdout else "None")
                f.write(e.stderr)
                f.write("\n\n")

            self.log.error(f"❌ Reposition Failed:\n{e.stderr}")
            return (False, e.stderr)

    def calcNewTick(self, currentPrice):
        """_summary_
            currentPrice に基づいてレンジを計算

        Returns:
            return (newTickLower, newTickUpper)
        """
        tickSpacing = 60
        currentTick = int(math.log(currentPrice / 1e12, 1.0001))

        halfWidthTicks = 300  # 3.0%

        newTickLower = currentTick - halfWidthTicks
        newTickUpper = currentTick + halfWidthTicks

        newTickLower = (newTickLower // tickSpacing) * tickSpacing
        newTickUpper = (newTickUpper // tickSpacing) * tickSpacing

        return (currentTick, newTickLower, newTickUpper)

    def calc_approx_swap_amount(
        self, current_price, current_tick, wallet_weth_wei=0, wallet_usdc_raw=0
    ):
        """
        リポジション時の理想的なスワップ量（概算）と方向を計算する
        """

        # 1. TickからSqrtPriceX96を計算する内部関数
        def tick_to_sqrt_price_x96(tick):
            return int(math.sqrt(1.0001**tick) * (2**96))

        sqrt_p_x96 = tick_to_sqrt_price_x96(current_tick)
        sqrt_p_a_x96 = tick_to_sqrt_price_x96(self.tickLower)
        sqrt_p_b_x96 = tick_to_sqrt_price_x96(self.tickUpper)

        # 2. 古いポジションから戻ってくるトークン量を推定 (Uniswap V3 Math)
        amount0_withdrawn = 0  # WETH (Wei)
        amount1_withdrawn = 0  # USDC (Raw)

        if sqrt_p_x96 <= sqrt_p_a_x96:
            amount0_withdrawn = (
                self.liquidity
                * ((sqrt_p_b_x96 - sqrt_p_a_x96) * (2**96))
                // (sqrt_p_b_x96 * sqrt_p_a_x96)
            )
        elif sqrt_p_x96 < sqrt_p_b_x96:
            amount0_withdrawn = (
                self.liquidity
                * ((sqrt_p_b_x96 - sqrt_p_x96) * (2**96))
                // (sqrt_p_b_x96 * sqrt_p_x96)
            )
            amount1_withdrawn = self.liquidity * (sqrt_p_x96 - sqrt_p_a_x96) // (2**96)
        else:
            amount1_withdrawn = (
                self.liquidity * (sqrt_p_b_x96 - sqrt_p_a_x96) // (2**96)
            )

        # 3. リポジション時の手持ち総資金（Wallet残高 + 引き出し額）
        total_weth_wei = wallet_weth_wei + amount0_withdrawn
        total_usdc_raw = wallet_usdc_raw + amount1_withdrawn

        # 4. 現在の価格ベースでドル（USDC）換算の価値を出す
        weth_value_in_usdc = (total_weth_wei / 1e18) * current_price
        usdc_value_in_usdc = total_usdc_raw / 1e6

        total_value = weth_value_in_usdc + usdc_value_in_usdc
        target_value = total_value / 2.0  # 理想は50:50

        # 5. 差額からスワップすべき量を計算
        swap_zero_for_one = "0"
        swap_amount = 0

        # WETHが多すぎる場合 -> WETHを売る (0 for 1)
        if weth_value_in_usdc > target_value:
            excess_weth_value = weth_value_in_usdc - target_value

            # 💡 差額が1ドル未満ならガス代の無駄なのでスワップしない
            if excess_weth_value > 1.0:
                weth_to_sell = excess_weth_value / current_price
                swap_zero_for_one = "1"
                # 支払う(Exact Input)なのでマイナス値にする
                swap_amount = -int(weth_to_sell * 1e18)

        # USDCが多すぎる場合 -> USDCを売る (1 for 0)
        elif usdc_value_in_usdc > target_value:
            excess_usdc_value = usdc_value_in_usdc - target_value

            if excess_usdc_value > 1.0:
                swap_zero_for_one = "0"
                swap_amount = -int(excess_usdc_value * 1e6)

        return swap_zero_for_one, swap_amount

    def getSqrtPriceX96fromUSDCPrice(self, inCexPrice: float):

        rawSqrtPrice = math.sqrt(inCexPrice * 1e-12)
        sqrtPriceX96 = int(rawSqrtPrice * (2**96))

        return sqrtPriceX96

    def calcExpectedOut(
        self, inAbsAmount, inZeroForOne, inCurrentPrice, inSlippageTolerance=0.01
    ):
        if inZeroForOne == "1":
            # WETHを売ってUSDCを買う
            idealOut = (inAbsAmount / 1e18) * inCurrentPrice * 1e6
            retOut = int(idealOut * (1.0 - inSlippageTolerance))
        else:
            # USDCを売ってWETHを買う
            idealOut = (inAbsAmount / 1e6) / inCurrentPrice * 1e18
            retOut = int(idealOut * (1.0 - inSlippageTolerance))

        return retOut

    def executeReposition(self, rpcURL, inCurrentPrice, isLiquidityZero):
        """_summary_

        Args:
            rpcURL (_type_): _description_
            inCurrentPrice (_type_): _description_
            isLiquidityZero (bool): isLiquidityZero(Enum).value を受け取る

        Returns:
            _type_: _description_
        """
        # 新規レンジを計算
        ticks = self.calcNewTick(currentPrice=inCurrentPrice)
        CurrentTick = ticks[0]
        TickLower = ticks[1]
        TickUpper = ticks[2]

        currentSqrtPriceX96 = self.getSqrtPriceX96fromUSDCPrice(inCurrentPrice)

        env_vars = os.environ.copy()

        # 概算スワップ料を計算
        swap_zero_for_one, swap_amount = self.calc_approx_swap_amount(
            inCurrentPrice, CurrentTick
        )

        # 許容最小値を計算
        expectedOut = self.calcExpectedOut(
            abs(swap_amount),
            swap_zero_for_one,
            inCurrentPrice,
            inSlippageTolerance=0.005,
        )

        self.log.info(
            f"Swap Required: zeroForOne={swap_zero_for_one}, amount={swap_amount}"
        )

        # 環境変数の設定
        env_vars["PRIVATE_KEY"] = "0x" + str(self.privateKey)
        env_vars["DYNAMIC_OLD_LOWER"] = str(self.tickLower)
        env_vars["DYNAMIC_OLD_UPPER"] = str(self.tickUpper)
        env_vars["DYNAMIC_NEW_LOWER"] = str(TickLower)
        env_vars["DYNAMIC_NEW_UPPER"] = str(TickUpper)
        env_vars["EXACT_LIQUIDITY"] = str(self.liquidity)
        env_vars["SQRT_PRICE"] = str(currentSqrtPriceX96)
        env_vars["SWAP_ZERO_FOR_ONE"] = str(swap_zero_for_one)
        env_vars["SWAP_AMOUNT"] = str(swap_amount)
        env_vars["DYNAMIC_SWAP_MIN_OUT"] = str(expectedOut)
        env_vars["IS_LIQUIDITY_ZERO"] = str(isLiquidityZero)

        command = [
            "forge",
            "script",
            "script/Reposition.s.sol:Reposition",
            "--rpc-url",
            rpcURL,
            "--broadcast",
            "--private-key",
            self.privateKey,
            "-vvvv",
        ]

        response = self.commandExecuter(command, env_vars)

        if response[0]:
            self.log.info(f"successffully positioned new PoolRange \n {response[1]}")

            # リポジションに成功したらレンジの値を更新する
            self.tickLower = TickLower
            self.tickUpper = TickUpper

            return True

        else:
            self.log.error(f"Pool Repositioning FAILED \n {response[1]}")
            return False
