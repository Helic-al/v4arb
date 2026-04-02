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

contract TestSwapReal is Script {
    using CurrencyLibrary for Currency;

    address constant POOL_MANAGER = 0xe54aCE66bD482c5781c9F69f89273586975FFcAC;
    
    // ★ あなたのHookアドレスを忘れずに入力してください
    address constant HOOK_ADDRESS = 0x25962c0d49b701932A9D7FD36C50A897e263c080; 
    
    address constant WETH_ADDRESS = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDC_ADDRESS = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;

    int24 constant TICK_SPACING = 60;
    
    // ★ 先ほどデプロイ時に合わせた LPFeeLibrary.DYNAMIC_FEE_FLAGを利用
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
        
        console.log("Swap Router deployed at:", address(swapRouter));

        // 極小額 0.0001 WETH (約30円分) をルーターにApprove
        uint256 swapAmount = 0.0001 ether;
        IERC20(WETH_ADDRESS).approve(address(swapRouter), type(uint256).max);

        // WETH(Token0) を支払って USDC(Token1) を受け取る
        bool zeroForOne = true;
        
        SwapParams memory params = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(swapAmount), // 支払うのでマイナス指定
            sqrtPriceLimitX96: zeroForOne 
                ? TickMath.MIN_SQRT_PRICE + 1 
                : TickMath.MAX_SQRT_PRICE - 1
        });

        PoolSwapTest.TestSettings memory testSettings = PoolSwapTest.TestSettings({
            takeClaims: false,
            settleUsingBurn: false
        });

        console.log("Testing 10% Launch Protection Swap...");
        
        swapRouter.swap(
            key,
            params,
            testSettings,
            ""
        );

        console.log("Test Swap Successful!");
        vm.stopBroadcast();
    }
}