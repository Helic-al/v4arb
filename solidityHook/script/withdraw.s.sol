// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {IPoolManager, ModifyLiquidityParams} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {CurrencyLibrary, Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";

// V4公式の流動性追加用ルーター
import {PoolModifyLiquidityTest} from "@uniswap/v4-core/src/test/PoolModifyLiquidityTest.sol";

contract WithdrawRealPool is Script {
    using CurrencyLibrary for Currency;

    address constant POOL_MANAGER = 0xe54aCE66bD482c5781c9F69f89273586975FFcAC;
    
    // ★ あなたのHookアドレス
    address constant HOOK_ADDRESS = 0x351d40e706339c7D7588B6F915d62D42510fC080;     
    // ★ 【超重要】前回のログに出力されたルーターアドレスをここに貼ってください
    address constant OLD_ROUTER = 0x264C16Cd53412181c83B518e72d01a57ebfcF2bD; 

    address constant WETH_ADDRESS = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDC_ADDRESS = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;

    int24 constant TICK_SPACING = 60;
    uint24 constant LP_FEE = LPFeeLibrary.DYNAMIC_FEE_FLAG; // 固定で作成してしまった500を指定してプールを特定します

    function run() external {
        vm.startBroadcast();

        Currency token0 = Currency.wrap(WETH_ADDRESS);
        Currency token1 = Currency.wrap(USDC_ADDRESS);

        PoolKey memory key = PoolKey({
            currency0: token0,
            currency1: token1,
            fee: LP_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(HOOK_ADDRESS)
        });

        int24 tickLower = -200280;
        int24 tickUpper = -199980;
            
        // ★ あなたがArbiscanのログで確認した正確な流動性（liquidity）の数値
        // これをそのままマイナスにして全額引き出します
        uint128 exactLiquidity = 3312036312811000;

        console.log("Withdrawing liquidity...");

        PoolModifyLiquidityTest(OLD_ROUTER).modifyLiquidity(
            key,
            ModifyLiquidityParams({
                tickLower: tickLower,
                tickUpper: tickUpper,
                liquidityDelta: -int256(uint256(exactLiquidity)), // マイナス指定で引き出し
                salt: bytes32(0)
            }),
            ""
        );

        console.log("Withdrawal Successful!");
        vm.stopBroadcast();
    }
}