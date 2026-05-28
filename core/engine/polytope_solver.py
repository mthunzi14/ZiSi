"""
polytope_solver.py - ZiSi Zero-Dependency Ultra-High-Speed Simplex & Polytope Projection Solver.
Uses highly optimized vector operations in Pure NumPy for sub-millisecond execution.
"""
import numpy as np
import logging
from typing import List, Tuple, Optional

log = logging.getLogger("zisi.math")

def kl_divergence(mu: np.ndarray, theta: np.ndarray, eps: float = 1e-9) -> float:
    """
    Calculate Kullback-Leibler (Bregman) Divergence under the binary entropy cost function.
    D(mu || theta) = sum( mu * ln(mu/theta) + (1-mu) * ln((1-mu)/(1-theta)) )
    """
    mu_clamped = np.clip(mu, eps, 1.0 - eps)
    theta_clamped = np.clip(theta, eps, 1.0 - eps)
    div = mu_clamped * np.log(mu_clamped / theta_clamped) + (1.0 - mu_clamped) * np.log((1.0 - mu_clamped) / (1.0 - theta_clamped))
    return float(np.sum(div))

def kl_gradient(mu: np.ndarray, theta: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """
    Calculate the gradient of KL Divergence with respect to mu.
    grad = ln(mu/(1-mu)) - ln(theta/(1-theta))
    """
    mu_clamped = np.clip(mu, eps, 1.0 - eps)
    theta_clamped = np.clip(theta, eps, 1.0 - eps)
    return np.log(mu_clamped / (1.0 - mu_clamped)) - np.log(theta_clamped / (1.0 - theta_clamped))

def project_simplex(y: np.ndarray, s: float = 1.0) -> np.ndarray:
    """
    Project vector y onto the simplex: sum(x) = s, x_i >= 0
    Exact, highly efficient O(N log N) algorithm in Pure NumPy.
    """
    n_features = len(y)
    u = np.sort(y)[::-1]
    cssv = np.cumsum(u)
    ind = np.arange(n_features) + 1
    cond = u - (cssv - s) / ind > 0
    rho = np.count_nonzero(cond) - 1
    theta = (cssv[rho] - s) / (rho + 1)
    return np.maximum(y - theta, 0.0)

class PolytopeSolver:
    """
    Zero-dependency high-frequency Projected Gradient Descent (PGD) optimizer.
    Solves: min_mu D(mu || theta) subject to P(YES_i) + P(NO_i) = 1.0 and bounds [0, 1]
    """
    def __init__(self, num_vars: int):
        self.num_vars = num_vars

    def project_mutually_exclusive_groups(self, mu: np.ndarray, groups: List[List[int]]) -> np.ndarray:
        """
        Project variables onto multiple mutually exclusive groups (e.g. YES/NO pairs or multi-outcome sets).
        Each index in a group must sum to 1.0.
        """
        projected = mu.copy()
        for group in groups:
            if not group:
                continue
            group_vals = projected[group]
            proj_group = project_simplex(group_vals, 1.0)
            projected[group] = proj_group
        return projected

    def project(self, theta: np.ndarray, groups: List[List[int]], max_iter: int = 100, tol: float = 1e-5) -> Tuple[np.ndarray, float]:
        """
        Runs Projected Gradient Descent to find the Bregman Projection under KL divergence.
        """
        theta = np.clip(np.asarray(theta, dtype=float), 0.01, 0.99)
        mu = theta.copy()
        
        # Initial projection to satisfy constraints
        mu = self.project_mutually_exclusive_groups(mu, groups)
        
        # Line search / gradient descent parameters
        step_size = 0.1
        
        for i in range(max_iter):
            prev_mu = mu.copy()
            
            # Compute gradient of KL divergence
            grad = kl_gradient(mu, theta)
            
            # Gradient descent step
            mu = mu - step_size * grad
            
            # Project back onto constraints
            mu = self.project_mutually_exclusive_groups(mu, groups)
            mu = np.clip(mu, 0.001, 0.999)  # Stay within safe probability bounds
            
            # Convergence check
            if np.linalg.norm(mu - prev_mu) < tol:
                break
                
        profit = kl_divergence(mu, theta)
        return mu, round(profit, 6)

if __name__ == "__main__":
    # Test suite validation:
    # 3-way mutually exclusive prediction market (YES/NO/OTHER)
    # The sum of their probabilities MUST equal exactly 1.0
    groups = [[0, 1, 2]]
    
    # Say market prices are mispriced and sum to 1.15:
    theta = np.array([0.45, 0.40, 0.30])
    
    solver = PolytopeSolver(num_vars=3)
    projected, arb_value = solver.project(theta, groups)
    
    print("Zero-Dependency Pure NumPy Solver Tests:")
    print("Market Prices (Sum = 1.15):", theta)
    print("Projected Prices (Sum = 1.00):", projected, "Sum:", np.sum(projected))
    print("Max Extractable Arbitrage Profit:", arb_value)
