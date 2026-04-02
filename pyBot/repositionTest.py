import os

from dotenv import load_dotenv
from getSecret import get_secret_key
from mainbot import DeltaNeutralBotV4
from PoolRepositioner import PoolRepositioner

load_dotenv("./.env")

POOL_MANAGER_ADDRESS = os.environ.get("POOL_MANAGER_ADDRESS")
HOOK_ADDRESS = os.environ.get("HOOK_ADDRESS")
RPC_URL = os.environ.get("RPC_URL")
ARB_SECRET = get_secret_key()

liquidity = 0

dn4 = DeltaNeutralBotV4()
data = dn4.get_onchain_data()

pr = PoolRepositioner(
    POOL_MANAGER_ADDRESS,
    HOOK_ADDRESS,
    data["my_L"],
    data["tickLower"],
    data["tickUpper"],
    ARB_SECRET,
)

# テスト用に小さな値を用いる
need_to_sell_weth = True
swap_amount_wei = -int(0.001 * 1e18)

swap_zero_for_one = "1" if need_to_sell_weth else "0"

response = pr.executeReposition(
    RPC_URL, data["price"], data["sqrtP_raw"], swap_zero_for_one, swap_amount_wei
)

print(response)
