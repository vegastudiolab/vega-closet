#!/usr/bin/env python3
# Image-similarity ranker (2026-11-13). A per-user logistic regression over CLIP image embeddings
# (public.clip_emb), fit on the user's own likes/carts vs passes and feed clears. Backtest 2026-10-14
# on Charles's history: AUC 0.788 and 21 of 74 held-out likes in a 300-card feed, vs 0.673 and 12 for
# the attribute model. Needs numpy; the builder falls back to the attribute model without it.
import base64
import numpy as np

def decode(e):
    return np.frombuffer(base64.b64decode(e), dtype=np.float16).astype(np.float32)

def fit(X, y, w=None, lam=2.0, iters=25):
    """L2 logistic regression by Newton/IRLS (penalty lam/2*||w||^2 on weights, intercept free).
    lam=2 matches the sklearn C=0.5 setting the backtest used. X: (n,d) float32, y: (n,) 0/1."""
    n, d = X.shape
    w = np.ones(n, dtype=np.float64) if w is None else np.asarray(w, dtype=np.float64)
    Xb = np.hstack([X.astype(np.float64), np.ones((n, 1))])
    beta = np.zeros(d + 1)
    R = lam * np.eye(d + 1); R[-1, -1] = 0.0
    for _ in range(iters):
        z = np.clip(Xb @ beta, -30, 30); p = 1.0 / (1.0 + np.exp(-z))
        g = Xb.T @ (w * (p - y)) + R @ beta
        H = (Xb * (w * p * (1 - p))[:, None]).T @ Xb + R
        step = np.linalg.solve(H, g)
        beta -= step
        if np.max(np.abs(step)) < 1e-6: break
    return beta

def predict(beta, X):
    z = np.clip(X.astype(np.float64) @ beta[:-1] + beta[-1], -30, 30)
    return 1.0 / (1.0 + np.exp(-z))
