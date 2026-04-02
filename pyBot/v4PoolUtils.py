from eth_abi import encode
from eth_abi.packed import encode_packed
from web3 import Web3


# ===================================================================
# PoolId 計算ユーティリティ
# ===================================================================
class poolUtils:
    """
    v4pool, hook周りの管理クラス
    """

    def __init__(self, poolsSlot, liquidityOffset):

        # StateLibrary定数設定
        self.poolsSlot = poolsSlot
        self.liquidityOffset = liquidityOffset

    def compute_pool_id(self, currency0, currency1, fee, tick_spacing, hooks):
        """PoolKey → PoolId (bytes32)"""
        encoded = Web3().codec.encode(
            ["address", "address", "uint24", "int24", "address"],
            [currency0, currency1, fee, tick_spacing, hooks],
        )
        return Web3.keccak(encoded)

    def get_pool_state_slot(self, pool_id_bytes):
        """StateLibrary._getPoolStateSlot(): keccak256(abi.encodePacked(poolId, POOLS_SLOT))"""
        pools_slot_bytes = pool_id_bytes + self.poolsSlot.to_bytes(32, "big")
        return Web3.keccak(pools_slot_bytes)

    def read_slot0_via_extsload(self, pm_contract, pool_id_bytes):
        """extsload 経由で slot0 を読む (StateLibrary.getSlot0 互換)"""
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        data = pm_contract.functions.extsload(state_slot).call()
        data_int = int.from_bytes(data, "big")

        sqrtPriceX96 = data_int & ((1 << 160) - 1)
        tick_raw = (data_int >> 160) & 0xFFFFFF
        tick = tick_raw - 0x1000000 if tick_raw >= 0x800000 else tick_raw

        return sqrtPriceX96, tick

    def get_current_fee_via_extsload(self, pm_contract, pool_id_bytes):
        """slot0から現在の適用手数料(lpFee)を取得"""
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        data = pm_contract.functions.extsload(state_slot).call()
        data_int = int.from_bytes(data, "big")

        # V4のslot0構造: sqrtPrice(160bit) + tick(24bit) + protocolFee(16bit) + lpFee(24bit)
        # 右に200ビットシフトして24ビット分を取り出す
        lp_fee = (data_int >> 200) & 0xFFFFFF
        return lp_fee

    def read_liquidity_via_extsload(self, pm_contract, pool_id_bytes):
        """extsload 経由で liquidity を読む (StateLibrary.getLiquidity 互換)"""
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        liq_slot = (int.from_bytes(state_slot, "big") + self.liquidityOffset).to_bytes(
            32, "big"
        )
        data = pm_contract.functions.extsload(liq_slot).call()
        return int.from_bytes(data, "big") & ((1 << 128) - 1)

    def read_fee_globals_via_extsload(self, pm_contract, pool_id_bytes):
        """
        プールの全体手数料(feeGrowthGlobal)を取得
        """
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        # feeGrowthGlobal0X128 は offset 1, feeGrowthGlobal1X128 は offset 2
        fg0_slot = (int.from_bytes(state_slot, "big") + 1).to_bytes(32, "big")
        fg1_slot = (int.from_bytes(state_slot, "big") + 2).to_bytes(32, "big")

        fg0 = int.from_bytes(pm_contract.functions.extsload(fg0_slot).call(), "big")
        fg1 = int.from_bytes(pm_contract.functions.extsload(fg1_slot).call(), "big")
        return fg0, fg1

    def get_tick_fee_outside_via_extsload(self, pm_contract, pool_id_bytes, tick):
        """特定のTickの境界手数料(feeGrowthOutside)を取得"""
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        ticks_mapping_slot = int.from_bytes(state_slot, "big") + 4

        # mapping(int24 => Tick.Info) のスロット計算
        tick_slot_bytes = Web3.keccak(
            encode(["int24", "uint256"], [tick, ticks_mapping_slot])
        )

        # feeGrowthOutside0X128 は offset 1, feeGrowthOutside1X128 は offset 2
        fg0_slot = (int.from_bytes(tick_slot_bytes, "big") + 1).to_bytes(32, "big")
        fg1_slot = (int.from_bytes(tick_slot_bytes, "big") + 2).to_bytes(32, "big")

        fg0 = int.from_bytes(pm_contract.functions.extsload(fg0_slot).call(), "big")
        fg1 = int.from_bytes(pm_contract.functions.extsload(fg1_slot).call(), "big")
        return fg0, fg1

    def get_position_fee_inside_last_via_extsload(
        self, pm_contract, pool_id_bytes, owner, tick_lower, tick_upper, salt
    ):
        """ポジション作成・更新時の基準手数料(feeGrowthInsideLast)を取得"""
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        pos_mapping_slot = int.from_bytes(state_slot, "big") + 6

        # positionKey = keccak256(abi.encodePacked(owner, tickLower, tickUpper, salt))
        pos_key = Web3.keccak(
            encode_packed(
                ["address", "int24", "int24", "bytes32"],
                [owner, tick_lower, tick_upper, salt],
            )
        )

        pos_slot_bytes = Web3.keccak(
            encode(["bytes32", "uint256"], [pos_key, pos_mapping_slot])
        )

        # V4 Position.State 構造体: offset 0 = fee0Last, offset 1 = fee1Last
        fg0_slot = (int.from_bytes(pos_slot_bytes, "big") + 1).to_bytes(32, "big")
        fg1_slot = (int.from_bytes(pos_slot_bytes, "big") + 2).to_bytes(32, "big")

        fg0 = int.from_bytes(pm_contract.functions.extsload(fg0_slot).call(), "big")
        fg1 = int.from_bytes(pm_contract.functions.extsload(fg1_slot).call(), "big")
        return fg0, fg1

    def get_position_liquidity_via_extsload(
        self, pm_contract, pool_id_bytes, owner, tick_lower, tick_upper, salt
    ):
        """自分の特定のポジションの現在の正確なLiquidity(流動性量)を取得"""
        state_slot = self.get_pool_state_slot(pool_id_bytes)
        pos_mapping_slot = int.from_bytes(state_slot, "big") + 6

        # positionKey を計算
        pos_key = Web3.keccak(
            encode_packed(
                ["address", "int24", "int24", "bytes32"],
                [owner, tick_lower, tick_upper, salt],
            )
        )

        pos_slot_bytes = Web3.keccak(
            encode(["bytes32", "uint256"], [pos_key, pos_mapping_slot])
        )

        # V4 Position.State 構造体の offset 0 に liquidity (uint128) が格納されている
        data = pm_contract.functions.extsload(pos_slot_bytes).call()
        liquidity = int.from_bytes(data, "big") & ((1 << 128) - 1)
        return liquidity
