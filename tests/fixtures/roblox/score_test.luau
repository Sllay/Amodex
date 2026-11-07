--!strict
-- Synthetic pure logic: no services, network, persistence, purchases or publishing.
local function score(coins: number, multiplier: number): number
    return math.clamp(coins, 0, 100) * multiplier
end
assert(score(7, 6) == 42, "normal score")
assert(score(-1, 6) == 0, "lower bound")
assert(score(150, 2) == 200, "upper bound")
print("AMODEX_LUAU_OK checks=3 score=42")
return { ok = true, checks = 3, score = score(7, 6) }
