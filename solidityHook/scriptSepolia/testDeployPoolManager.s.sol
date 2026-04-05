// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {PoolManager} from "@uniswap/v4-core/src/PoolManager.sol";

contract DeployPoolManagerScript is Script {
    function run() public {
        vm.startBroadcast();

        // PoolManagerのデプロイ (500kガス程度)
        // ※コンストラクタ引数は通常 500000 (ControllerGasLimit) ですが
        // 最新版では引数なしか、変更されている場合があります。
        // 標準的な実装(v4-core)では引数として `controllerGasLimit` を渡すことが多いです。
        PoolManager manager = new PoolManager(msg.sender);
        
        console.log("PoolManager Deployed at:", address(manager));

        vm.stopBroadcast();
    }
}