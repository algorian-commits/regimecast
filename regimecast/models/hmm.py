"""Gaussian hidden Markov model (Hamilton 1989), small and dependency-free.

Why not hmmlearn: it has no wheel for Python 3.14 on Windows (needs a C++ compiler),
and we need a *forward-only* filter anyway. The usual ``predict_proba`` runs the
forward-backward pass, which uses future observations to label the past: fine for
describing history, look-ahead for anything we would act on.

* ``fit``: Baum-Welch (EM) in log space, full covariances, several random starts,
  the best log-likelihood wins.
* ``filter``: P(state at t | observations up to t), one step at a time.
* ``order_by``: relabel states so state 0 has the lowest mean of a chosen feature
  (we use log realised volatility: 0 = calm, 1 = active, 2 = stressed).
"""
from __future__ import annotations

import numpy as np


def _log_gauss(X: np.ndarray, mean: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """log N(x | mean, cov) for every row of X."""
    d = X.shape[1]
    L = np.linalg.cholesky(cov)
    z = np.linalg.solve(L, (X - mean).T)
    return -0.5 * (d * np.log(2 * np.pi) + 2 * np.log(np.diag(L)).sum() + (z ** 2).sum(axis=0))


class GaussianHMM:
    def __init__(self, n_states: int = 3, n_iter: int = 200, tol: float = 1e-5, n_init: int = 5,
                 seed: int = 0, reg: float = 1e-4, sticky: float = 0.9):
        self.k, self.n_iter, self.tol, self.n_init, self.seed, self.reg = n_states, n_iter, tol, n_init, seed, reg
        self.sticky = sticky  # initial self-transition probability

    # ---------------------------------------------------------- pieces
    def log_emission(self, X: np.ndarray) -> np.ndarray:
        """log P(x_t | state). Rows with a missing value give 0 for every state: the
        filter then only moves the state forward in time, with no evidence update."""
        X = np.asarray(X, float)
        ok = np.isfinite(X).all(axis=1)
        out = np.zeros((len(X), self.k))
        if ok.any():
            out[ok] = np.column_stack([_log_gauss(X[ok], self.means_[j], self.covs_[j]) for j in range(self.k)])
        return out

    def _scaled(self, logB: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        """Scaled forward pass (Rabiner 1989): alpha[t] = P(state_t | x_1..x_t)."""
        m = logB.max(axis=1, keepdims=True)
        B = np.exp(logB - m)                       # rescaled per row; added back in the log-likelihood
        T, K = B.shape
        A = self.transmat_
        alpha, c = np.empty((T, K)), np.empty(T)
        a = self.startprob_ * B[0]
        c[0] = a.sum()
        alpha[0] = a / c[0]
        for t in range(1, T):
            a = (alpha[t - 1] @ A) * B[t]
            c[t] = a.sum()
            alpha[t] = a / c[t]
        return alpha, B, c, float(np.log(c).sum() + m.sum())

    def _beta(self, B: np.ndarray, c: np.ndarray) -> np.ndarray:
        T, K = B.shape
        A = self.transmat_
        beta = np.ones((T, K))
        for t in range(T - 2, -1, -1):
            beta[t] = (A @ (B[t + 1] * beta[t + 1])) / c[t + 1]
        return beta

    def _init_params(self, X: np.ndarray, rng) -> None:
        n, d = X.shape
        # k-means++ style seeds, then a few Lloyd steps
        centers = [X[rng.integers(n)]]
        for _ in range(1, self.k):
            d2 = np.min([((X - c) ** 2).sum(1) for c in centers], axis=0)
            centers.append(X[rng.choice(n, p=d2 / d2.sum())])
        C = np.array(centers)
        for _ in range(10):
            lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
            C = np.array([X[lab == j].mean(0) if (lab == j).any() else C[j] for j in range(self.k)])
        cov = np.cov(X.T).reshape(d, d) + self.reg * np.eye(d)
        self.means_ = C
        self.covs_ = np.array([np.cov(X[lab == j].T).reshape(d, d) + self.reg * np.eye(d)
                               if (lab == j).sum() > d + 1 else cov for j in range(self.k)])
        self.startprob_ = np.full(self.k, 1 / self.k)
        off = (1 - self.sticky) / (self.k - 1)
        self.transmat_ = np.full((self.k, self.k), off) + np.eye(self.k) * (self.sticky - off)

    def _em(self, X: np.ndarray) -> float:
        d = X.shape[1]
        prev = -np.inf
        for _ in range(self.n_iter):
            alpha, B, c, ll = self._scaled(self.log_emission(X))
            beta = self._beta(B, c)
            g = alpha * beta                                        # P(state_t | all x)
            xi = (alpha[:-1].T @ (B[1:] * beta[1:] / c[1:, None])) * self.transmat_
            self.startprob_ = np.clip(g[0], 1e-12, None) / np.clip(g[0], 1e-12, None).sum()
            self.transmat_ = np.clip(xi, 1e-12, None) / np.clip(xi, 1e-12, None).sum(1, keepdims=True)
            w = g.sum(0)
            self.means_ = (g.T @ X) / w[:, None]
            for j in range(self.k):
                Z = X - self.means_[j]
                self.covs_[j] = (g[:, j, None] * Z).T @ Z / w[j] + self.reg * np.eye(d)
            if ll - prev < self.tol * abs(ll):
                break
            prev = ll
        return ll

    # ---------------------------------------------------------- public
    def fit(self, X: np.ndarray, warm: "GaussianHMM | None" = None) -> "GaussianHMM":
        """warm: a previously fitted model to use as one extra start (refits in walk-forward)."""
        X = np.asarray(X, float)
        rng = np.random.default_rng(self.seed)
        best, best_ll = None, -np.inf
        starts = (["warm"] if warm is not None else []) + ["random"] * self.n_init
        for kind in starts:
            if kind == "warm":
                self.startprob_, self.transmat_ = warm.startprob_.copy(), warm.transmat_.copy()
                self.means_, self.covs_ = warm.means_.copy(), warm.covs_.copy()
            else:
                self._init_params(X, rng)
            try:
                ll = self._em(X)
            except np.linalg.LinAlgError:
                continue
            if ll > best_ll:
                best_ll = ll
                best = (self.startprob_.copy(), self.transmat_.copy(), self.means_.copy(), self.covs_.copy())
        if best is None:
            raise RuntimeError("HMM fit failed for every start")
        self.startprob_, self.transmat_, self.means_, self.covs_ = best
        self.loglik_ = best_ll
        return self

    def order_by(self, feature: int) -> "GaussianHMM":
        o = np.argsort(self.means_[:, feature])
        self.means_, self.covs_, self.startprob_ = self.means_[o], self.covs_[o], self.startprob_[o]
        self.transmat_ = self.transmat_[np.ix_(o, o)]
        return self

    def filter(self, X: np.ndarray) -> np.ndarray:
        """P(state_t | x_1..x_t) for every t. Causal: row t never uses rows after t."""
        return self._scaled(self.log_emission(np.asarray(X, float)))[0]

    def smooth(self, X: np.ndarray) -> np.ndarray:
        """P(state_t | all x). Uses the future: for describing history only."""
        alpha, B, c, _ = self._scaled(self.log_emission(np.asarray(X, float)))
        return alpha * self._beta(B, c)

    def score(self, X: np.ndarray) -> float:
        return self._scaled(self.log_emission(np.asarray(X, float)))[3]
