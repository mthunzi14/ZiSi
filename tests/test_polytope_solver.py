import unittest
import numpy as np
from core.engine.polytope_solver import PolytopeSolver, kl_divergence, kl_gradient, project_simplex

class TestPolytopeSolver(unittest.TestCase):
    def test_kl_divergence(self):
        mu = np.array([0.5, 0.5])
        theta = np.array([0.5, 0.5])
        # Direct matching should yield exactly 0.0 KL divergence
        self.assertAlmostEqual(kl_divergence(mu, theta), 0.0)
        
        # High mispricing yields positive divergence
        theta_mismatched = np.array([0.1, 0.9])
        self.assertGreater(kl_divergence(mu, theta_mismatched), 0.0)

    def test_kl_gradient(self):
        mu = np.array([0.5, 0.5])
        theta = np.array([0.5, 0.5])
        grad = kl_gradient(mu, theta)
        np.testing.assert_array_almost_equal(grad, np.zeros(2))

    def test_project_simplex(self):
        # A vector that sums to 1.15 should be projected onto sum = 1.0
        y = np.array([0.60, 0.45])
        projected = project_simplex(y, 1.0)
        self.assertAlmostEqual(np.sum(projected), 1.0)
        self.assertTrue(np.all(projected >= 0.0))

    def test_polytope_solver_projection(self):
        solver = PolytopeSolver(num_vars=3)
        groups = [[0, 1, 2]]
        
        # Mispriced probabilities summing to 1.15
        theta = np.array([0.45, 0.40, 0.30])
        projected, arb_value = solver.project(theta, groups)
        
        # Must project onto constraints (sum to exactly 1.0)
        self.assertAlmostEqual(np.sum(projected), 1.0)
        self.assertTrue(np.all(projected >= 0.001))
        self.assertGreater(arb_value, 0.0)

if __name__ == "__main__":
    unittest.main()
