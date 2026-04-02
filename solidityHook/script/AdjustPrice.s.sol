// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {IPoolManager, SwapParams} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {CurrencyLibrary, Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {PoolSwapTest} from "@uniswap/v4-core/src/test/PoolSwapTest.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
}

contract AdjustPrice is Script {
    using CurrencyLibrary for Currency;

    address constant POOL_MANAGER = 0xe54aCE66bD482c5781c9F69f89273586975FFcAC;
    
    // ★ あなたのHookアドレス
    address constant HOOK_ADDRESS = 0xF55DD6e6be1acb02E05c24dE345a13f6Efcd0080; 
    
    address constant WETH_ADDRESS = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDC_ADDRESS = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;

    int24 constant TICK_SPACING = 60;
    uint24 constant LP_FEE = LPFeeLibrary.DYNAMIC_FEE_FLAG; 

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

        IPoolManager manager = IPoolManager(POOL_MANAGER);
        PoolSwapTest swapRouter = new PoolSwapTest(manager);
        
        // --------------------------------------------------------
        // ★ 調整量: まずは 1 USDC だけスワップして様子を見る
        // --------------------------------------------------------
        uint256 swapAmount =  5 * 1e6; // 1 USDC
        
        IERC20(USDC_ADDRESS).approve(address(swapRouter), type(uint256).max);

        // false = USDCを払ってWETHをもらう (価格が上がる)
        bool zeroForOne = false; 


        SwapParams memory params = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(swapAmount), // マイナス指定＝この額をきっちり支払う
            sqrtPriceLimitX96: TickMath.MAX_SQRT_PRICE - 1 // リミットなし（上限まで）
        });

        
        // // --------------------------------------------------------
        // // 逆注文を行う場合
        // // 1. 調整量: WETHの単位 (ether = 1e18) で指定します
        // // 例: 0.001 WETH をスワップする場合
        // // --------------------------------------------------------
        // uint256 swapAmount = 0.05 ether; 
        
        // // --------------------------------------------------------
        // // 2. Approve: 支払うトークンである WETH を許可します
        // // --------------------------------------------------------
        // IERC20(WETH_ADDRESS).approve(address(swapRouter), type(uint256).max);

        // // --------------------------------------------------------
        // // 3. 方向フラグ: true = WETH(Token0)を払ってUSDC(Token1)をもらう (価格が下がる)
        // // --------------------------------------------------------
        // bool zeroForOne = true;

        // // --------------------------------------------------------
        // // 4. パラメータ設定: 下落方向なのでリミットを MIN_SQRT_PRICE に変更します
        // // --------------------------------------------------------
        // SwapParams memory params = SwapParams({
        //     zeroForOne: zeroForOne,
        //     amountSpecified: -int256(swapAmount), // マイナス指定＝この額をきっちり支払う
            
        //     // ★ここが超重要: zeroForOneがtrueの場合は下限(MIN)を指定する
        //     sqrtPriceLimitX96: TickMath.MIN_SQRT_PRICE + 1 
        // });

        PoolSwapTest.TestSettings memory testSettings = PoolSwapTest.TestSettings({
            takeClaims: false,
            settleUsingBurn: false
        });

        console.log("Adjusting Price: Swapping USDC for WETH...");
        
        swapRouter.swap(
            key,
            params,
            testSettings,
            ""
        );

        console.log("Price Adjustment Swap Successful!");
        vm.stopBroadcast();
    }
}