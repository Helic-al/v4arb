// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";

// フック本体のインポート (パスはプロジェクト構成に合わせて調整してください)
import {DeltaNeutralHook} from "../src/DNHook.sol";

// Uniswap V4 関連のインポート
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";

// HookMinerのインポート (v4-peripheryにあります)
import {HookMiner} from "../src/HookMiner.sol";

contract DeployDeltaNeutralHook is Script {
    function run() public {
        // ---------------------------------------------------------
        // 1. 設定: PoolManagerのアドレスを入力sssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssしてください
        // ---------------------------------------------------------
        address poolManagerAddress = 0xe54aCE66bD482c5781c9F69f89273586975FFcAC; // 例: 取得済みのアドレスに置換

        // ---------------------------------------------------------
        // 2. フラグ定義: フックが必要とする権限
        // ---------------------------------------------------------
        // 今回のフックは `beforeSwap` のみ true です
        uint160 flags = uint160(Hooks.BEFORE_SWAP_FLAG);
        
        // ---------------------------------------------------------
        // 3. アドレスのマイニング (Saltの計算)
        // ---------------------------------------------------------
        // デプロイを行うウォレットアドレス (broadcast時はmsg.sender)
        address create2Deployer = 0x4e59b44847b379578588920cA78FbF26c0B4956C;

        //外部からパラメータ変更を行う際に必要なアドレス(ウォレットのもの)
        address MY_WALLET = vm.envAddress("MY_WALLET");

        // DeltaNetutralHookに渡す引数を作成
        bytes memory creationCode = type(DeltaNeutralHook).creationCode;
        // コンストラクタに渡す2つの引数（マネージャーとオーナー）をエンコード
        bytes memory constructorArgs = abi.encode(IPoolManager(poolManagerAddress), MY_WALLET);

        // HookMinerを使って、flags条件を満たすsaltを探す
        console.log("Mining hook address...");
        (address hookAddress, bytes32 salt) = HookMiner.find(
            create2Deployer,
            flags,
            creationCode,
            constructorArgs
        );

        console.log("Found salt:", vm.toString(salt));
        console.log("Expected Hook Address:", hookAddress);

        // ---------------------------------------------------------
        // 4. デプロイ実行
        // ---------------------------------------------------------
        vm.startBroadcast();

        // マイニングしたsaltを使ってデプロイ (CREATE2)
        DeltaNeutralHook hook = new DeltaNeutralHook{salt: salt}(
            IPoolManager(poolManagerAddress),
            MY_WALLET
        );

        vm.stopBroadcast();

        // 検証
        require(address(hook) == hookAddress, "Hook address mismatch!");
        console.log("Deploy successful!", address(hook));
    }
}