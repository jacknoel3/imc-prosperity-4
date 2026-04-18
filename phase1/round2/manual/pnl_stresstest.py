import numpy as np

def full_pnl(r, s, p, M, budget=50000):
    R = 200000 * np.log(1+r) / np.log(101)
    S = 7 * s / 100
    budget_used = (r + s + p) / 100 * budget
    return R * S * M - budget_used

r, s, p = 19.3, 57.7, 23   #put here your assumption for r, s, p
                           # r = research, s = sales, p = speed

for M in [0.9, 0.7, 0.5, 0.3, 0.1]:
    print(f"M={M}  →  PnL = {full_pnl(r,s,p,M):,.0f}")