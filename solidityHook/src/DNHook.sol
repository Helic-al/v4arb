// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "@uniswap/v4-periphery/src/BaseHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager, SwapParams} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";

contract DeltaNeutralHook is BaseHook {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    address public owner;
    mapping(PoolId => uint160) public lastPrices;

    uint24 public defaultFee = 300;    // 0.03%
    uint24 public highFee = 30000;     // 5%
    uint256 public whaleImpactThreshold = 2; // 流動性の2%以下の注文は通常手数料で通す
    uint256 public k = 78;
    uint256 public volatilityDivisor = 200;
    

    // ==========================================
    // ★ 追加機能: 初期保護モード (Launch Protection)
    // ==========================================
    bool public launchProtectionEnabled = true; // デプロイ直後は自動的にON
    uint24 public launchFee = 30000;           // 3% (ボットを確実に赤字にする超高額手数料)

    event MarketVolatile(PoolId indexed poolId, uint256 volatilityDiff, uint24 appliedFee);
    event ProtectionDisabled();

    error OnlyOwner();

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    constructor(IPoolManager _poolManager, address _initialOwner) BaseHook(_poolManager) {
        owner = _initialOwner;
    }


    // ownerにのみ外部変更を許可するsetter関数郡

    function setDefaultFee(uint24 _newFee) external onlyOwner {
        defaultFee = _newFee;
    }

    function setHighFee(uint24 _newFee) external onlyOwner {
        highFee = _newFee;
    }

    function setWhaleImpactThreshold(uint24 _newThreshold) external onlyOwner {
        whaleImpactThreshold = _newThreshold;
    }

    function setVolatilityDivisor(uint256 _newDivisor) external onlyOwner {
        volatilityDivisor = _newDivisor;
    }


    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: false,
            beforeSwap: true, // スワップ前の手数料計算のみ使用
            afterSwap: false,
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    // スワップ前に注文量などに応じて手数料を調整する
    function _beforeSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata params,
        bytes calldata
    ) internal override returns (bytes4, BeforeSwapDelta, uint24) {
        
        // ==========================================
        // ★ 保護モードONの場合は、無条件で3%の手数料を適用
        // ==========================================
        if (launchProtectionEnabled) {
            return (
                BaseHook.beforeSwap.selector,
                BeforeSwapDeltaLibrary.ZERO_DELTA,
                launchFee | LPFeeLibrary.OVERRIDE_FEE_FLAG
            );
        }

        // ==========================================
        // 通常モード: クジラ＆ボラティリティ検知
        // ==========================================
        PoolId poolId = key.toId();
        (uint160 currentSqrtPriceX96, , , ) = poolManager.getSlot0(poolId);
        uint128 liquidity = poolManager.getLiquidity(poolId);
        uint160 lastSqrtPriceX96 = lastPrices[poolId];

        uint24 newFee = defaultFee;
        bool highFeeTriggered = false;
 
        uint256 threshold = lastSqrtPriceX96 / volatilityDivisor;

        if (lastSqrtPriceX96 == 0) {
            lastPrices[poolId] = currentSqrtPriceX96;
        } else {
            uint256 diff = currentSqrtPriceX96 > lastSqrtPriceX96
                ? currentSqrtPriceX96 - lastSqrtPriceX96
                : lastSqrtPriceX96 - currentSqrtPriceX96;

            if (diff > threshold) {
                newFee = highFee;
                highFeeTriggered = true;
                emit MarketVolatile(poolId, diff, newFee);
            }

            if (diff > threshold || highFeeTriggered) {
                lastPrices[poolId] = currentSqrtPriceX96;
            }
        }

        // スワップ量の絶対値
        if (liquidity > 0) {
            uint256 absAmount = params.amountSpecified > 0
                ? uint256(params.amountSpecified)
                : uint256(-params.amountSpecified);

            // 流動性に換算
            uint256 liquidityImpact;
            if (params.zeroForOne) {
                liquidityImpact = (absAmount * uint256(currentSqrtPriceX96)) >> 96;
            } else {
                liquidityImpact = (absAmount << 96) / uint256(currentSqrtPriceX96);
            }

            // whaleImpactThresholdを超えていたら段階的に手数料を高く設定
            // TODO: ここの手数料をステップではなく二次近似したシグモイドで実装
            uint256 impactPercentage = (liquidityImpact * 100) / uint256(liquidity);

            if (impactPercentage  >= whaleImpactThreshold) {
              newFee = defaultFee + k*(impactPercentage - whaleImpactThreshold)^2;
              if (newFee > highFee) {
                newFee = highFee;
              }
              emit MarketVolatile(poolId, 0, newFee);
            } 


        return (
            BaseHook.beforeSwap.selector,
            BeforeSwapDeltaLibrary.ZERO_DELTA,
            newFee | LPFeeLibrary.OVERRIDE_FEE_FLAG
        );
    }

    // ==========================================
    // ★ オーナー専用関数: 価格が安定したらこれを実行して保護を解除する
    // ==========================================
    function disableLaunchProtection() external onlyOwner {
        launchProtectionEnabled = false;
        emit ProtectionDisabled();
    }

    // ★　オーナー権限譲渡関数
    function transferOwnership(address _newOwner) external onlyOwner{
        require(_newOwner != address(0), "New owner cannot be zero address.");
        owner = _newOwner;
    }
}
