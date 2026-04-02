// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";

// フックのインターフェース（解除関数のみ定義）
interface IDeltaNeutralHook {
    function disableLaunchProtection() external;
}

contract DisableProtection is Script {
    // ★ あなたのHookアドレスをここに入力してください
    address constant HOOK_ADDRESS = 0x351d40e706339c7D7588B6F915d62D42510fC080; 

    function run() external {
        vm.startBroadcast();

        console.log("Disabling Launch Protection...");
        
        // オーナー（デプロイしたあなた）の権限でシールド解除を実行
        IDeltaNeutralHook(HOOK_ADDRESS).disableLaunchProtection();
        
        console.log("Protection Disabled!");
        console.log("Pool is now LIVE with 0.05% base fee and dynamic defense system.");

        vm.stopBroadcast();
    }
}