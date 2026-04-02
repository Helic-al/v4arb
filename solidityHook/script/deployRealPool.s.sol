// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";

import {IPoolManager, ModifyLiquidityParams} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {CurrencyLibrary, Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {LiquidityAmounts} from "@uniswap/v4-core/test/utils/LiquidityAmounts.sol";

// ★ 追加: 動的手数料のフラグを読み込むためのライブラリ
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";

import {PoolModifyLiquidityTest} from "@uniswap/v4-core/src/test/PoolModifyLiquidityTest.sol";

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
}

contract DeployRealPool is Script {
    using CurrencyLibrary for Currency;

    address constant POOL_MANAGER = 0xe54aCE66bD482c5781c9F69f89273586975FFcAC;
    
    // ★ ここには【現在デプロイ済みのあなたのHookアドレス】をそのまま入れてください
    // 20260223 10:53 フック更新
    address constant HOOK_ADDRESS = 0x351d40e706339c7D7588B6F915d62D42510fC080; 

    address constant WETH_ADDRESS = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDC_ADDRESS = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;

    int24 constant TICK_SPACING = 60;

    // ------------------------------------------------------------------
    // ★ 修正点: ここが最も重要です！
    // 500 (固定) ではなく、0x800000 (動的手数料許可フラグ) を指定します
    // ------------------------------------------------------------------
    uint24 constant LP_FEE = LPFeeLibrary.DYNAMIC_FEE_FLAG;

    function run() external {
        vm.startBroadcast();

        Currency token0 = Currency.wrap(WETH_ADDRESS);
        Currency token1 = Currency.wrap(USDC_ADDRESS);

        // IPoolManager manager = IPoolManager(POOL_MANAGER);
        
        // あたらしくルーターアドレスが必要な場合にはnewする、それ以外は古いものを渡す
        // PoolModifyLiquidityTest lpRouter = new PoolModifyLiquidityTest(manager);
        // console.log("Liquidity Router deployed at:", address(lpRouter));
        address OLD_ROUTER = 0x264C16Cd53412181c83B518e72d01a57ebfcF2bD; 
        PoolModifyLiquidityTest lpRouter = PoolModifyLiquidityTest(OLD_ROUTER);
        console.log("Liquidity Router deployed at:", address(lpRouter));

        IERC20(WETH_ADDRESS).approve(address(lpRouter), type(uint256).max);
        IERC20(USDC_ADDRESS).approve(address(lpRouter), type(uint256).max);

        PoolKey memory key = PoolKey({
            currency0: token0,
            currency1: token1,
            fee: LP_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(HOOK_ADDRESS)
        });

        // 初期価格
        uint160 startingPrice = 3559540680042583383080960;
        

        // // 注意⚠　流動性追加の場合にはinitializeは行わない
        // manager.initialize(key, startingPrice);
        // console.log("Dynamic Fee Pool Initialized!");

        // 上下約10%の集中流動性レンジを指定
        int24 tickLower = -200400;
        int24 tickUpper = -200100;

        // 引き出して戻ってきた資金を再度投入します
        uint256 amount0Desired = 1.0 ether;
        uint256 amount1Desired = 2000 * 1e6;

        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            startingPrice,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            amount0Desired,
            amount1Desired
        );

        lpRouter.modifyLiquidity(
            key,
            ModifyLiquidityParams({
                tickLower: tickLower,
                tickUpper: tickUpper,
                liquidityDelta: int256(uint256(liquidity)),
                salt: bytes32(0)
            }),
            ""
        );

        console.log("Liquidity Added to Dynamic Pool Successfully!");
        vm.stopBroadcast();
    }
}