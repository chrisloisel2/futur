"""
Trading-aware loss functions.

Standard ML losses (MSE, CrossEntropy) are insufficient for trading because:
- They don't capture trading costs
- They don't penalize drawdowns
- They don't weight direction vs magnitude appropriately

This module implements losses optimized for real trading performance.
"""
import logging

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


class DirectionalLoss(nn.Module):
    """
    Loss that penalizes incorrect direction predictions.

    Focuses on sign(prediction) vs sign(true_return), not magnitude.
    """

    def __init__(self):
        super().__init__()

    def forward(
        self,
        pred_return: torch.Tensor,
        true_return: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            pred_return: [batch] predicted returns
            true_return: [batch] actual returns

        Returns:
            loss: scalar
        """
        # Direction agreement: +1 if same sign, -1 if opposite
        pred_direction = torch.sign(pred_return)
        true_direction = torch.sign(true_return)

        # Agreement: 1 for correct, 0 for neutral, -1 for wrong
        agreement = pred_direction * true_direction

        # Loss: negative average agreement (minimize when directions match)
        loss = -agreement.mean()

        return loss


class MagnitudeWeightedMSE(nn.Module):
    """
    MSE loss weighted by magnitude of true returns.

    Large moves should be predicted more accurately than small moves.
    """

    def __init__(self, temperature: float = 1.0):
        """
        Args:
            temperature: Controls weight sensitivity (higher = more focus on large moves)
        """
        super().__init__()
        self.temperature = temperature

    def forward(
        self,
        pred_return: torch.Tensor,
        true_return: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            pred_return: [batch] predicted returns
            true_return: [batch] actual returns

        Returns:
            loss: scalar
        """
        # Weight by magnitude of true return
        weights = torch.abs(true_return) * self.temperature

        # Weighted MSE
        squared_error = (pred_return - true_return) ** 2
        weighted_loss = (weights * squared_error).mean()

        return weighted_loss


class TradingCostPenalty(nn.Module):
    """
    Penalty for excessive trading (position changes).

    Encourages the model to make confident, stable predictions rather than
    constantly flipping positions.
    """

    def __init__(self, trading_fee: float = 0.001):
        """
        Args:
            trading_fee: Transaction cost as fraction (e.g., 0.001 = 0.1%)
        """
        super().__init__()
        self.trading_fee = trading_fee

    def forward(
        self,
        pred_return: torch.Tensor,
        batch_size: int
    ) -> torch.Tensor:
        """
        Args:
            pred_return: [batch] predicted returns (sequential in time)
            batch_size: Batch size (for normalization)

        Returns:
            loss: scalar
        """
        # Positions from predictions
        positions = torch.sign(pred_return)

        # Count position changes
        position_changes = torch.abs(torch.diff(positions))

        # Turnover: average changes per sample
        turnover = position_changes.sum() / batch_size

        # Penalty: turnover * trading cost
        penalty = turnover * self.trading_fee

        return penalty


class DrawdownPenalty(nn.Module):
    """
    Penalty for cumulative drawdowns.

    Encourages the model to avoid sequences of losses that lead to large drawdowns.
    """

    def __init__(self, max_acceptable_drawdown: float = 0.1):
        """
        Args:
            max_acceptable_drawdown: Maximum acceptable drawdown as fraction (e.g., 0.1 = 10%)
        """
        super().__init__()
        self.max_acceptable_drawdown = max_acceptable_drawdown

    def forward(
        self,
        pred_return: torch.Tensor,
        true_return: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            pred_return: [batch] predicted returns (sequential)
            true_return: [batch] actual returns (sequential)

        Returns:
            loss: scalar
        """
        # Predicted positions
        positions = torch.sign(pred_return)

        # Realized returns (position * true_return)
        realized_returns = positions * true_return

        # Cumulative returns
        cumulative_returns = torch.cumsum(realized_returns, dim=0)

        # Running maximum
        running_max = torch.cummax(cumulative_returns, dim=0)[0]

        # Drawdown at each point
        drawdown = running_max - cumulative_returns

        # Penalize drawdowns exceeding threshold
        excess_drawdown = torch.relu(drawdown - self.max_acceptable_drawdown)

        # Average excess drawdown
        penalty = excess_drawdown.mean()

        return penalty


class SharpeRatioLoss(nn.Module):
    """
    Negative Sharpe ratio as loss.

    Directly optimizes for risk-adjusted returns.

    Note: Can be noisy for small batches.
    """

    def __init__(self, risk_free_rate: float = 0.0):
        """
        Args:
            risk_free_rate: Annual risk-free rate (e.g., 0.02 = 2%)
        """
        super().__init__()
        self.risk_free_rate = risk_free_rate

    def forward(
        self,
        pred_return: torch.Tensor,
        true_return: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            pred_return: [batch] predicted returns
            true_return: [batch] actual returns

        Returns:
            loss: scalar (negative Sharpe ratio)
        """
        # Predicted positions
        positions = torch.sign(pred_return)

        # Realized returns
        realized_returns = positions * true_return

        # Mean and std of returns
        mean_return = realized_returns.mean()
        std_return = realized_returns.std() + 1e-8  # Avoid division by zero

        # Sharpe ratio (simplified, without annualization)
        sharpe = (mean_return - self.risk_free_rate) / std_return

        # Loss: negative Sharpe (maximize Sharpe = minimize negative Sharpe)
        loss = -sharpe

        return loss


class CompositeTradingLoss(nn.Module):
    """
    Composite loss combining multiple trading-aware objectives.

    L_total = α * L_direction + β * L_magnitude + γ * L_trading_cost + δ * L_drawdown

    This is the main loss function to use for TRM training.
    """

    def __init__(
        self,
        alpha: float = 1.0,  # Directional loss weight
        beta: float = 0.5,   # Magnitude loss weight
        gamma: float = 0.2,  # Trading cost penalty weight
        delta: float = 0.3,  # Drawdown penalty weight
        trading_fee: float = 0.001,
        max_acceptable_drawdown: float = 0.1,
        magnitude_temperature: float = 1.0
    ):
        """
        Args:
            alpha: Weight for directional loss
            beta: Weight for magnitude-weighted MSE
            gamma: Weight for trading cost penalty
            delta: Weight for drawdown penalty
            trading_fee: Transaction cost (e.g., 0.001 = 0.1%)
            max_acceptable_drawdown: Maximum acceptable drawdown fraction
            magnitude_temperature: Temperature for magnitude weighting
        """
        super().__init__()

        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.delta = delta

        # Component losses
        self.directional_loss = DirectionalLoss()
        self.magnitude_loss = MagnitudeWeightedMSE(temperature=magnitude_temperature)
        self.trading_cost_penalty = TradingCostPenalty(trading_fee=trading_fee)
        self.drawdown_penalty = DrawdownPenalty(max_acceptable_drawdown=max_acceptable_drawdown)

        logger.info(
            f"Initialized CompositeTradingLoss: "
            f"α={alpha}, β={beta}, γ={gamma}, δ={delta}"
        )

    def forward(
        self,
        pred_return: torch.Tensor,
        true_return: torch.Tensor
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        """
        Args:
            pred_return: [batch] predicted returns
            true_return: [batch] actual returns

        Returns:
            total_loss: scalar
            loss_components: dict with individual loss components (for logging)
        """
        batch_size = pred_return.shape[0]

        # Compute individual losses
        loss_dir = self.directional_loss(pred_return, true_return)
        loss_mag = self.magnitude_loss(pred_return, true_return)
        loss_cost = self.trading_cost_penalty(pred_return, batch_size)
        loss_dd = self.drawdown_penalty(pred_return, true_return)

        # Weighted combination
        total_loss = (
            self.alpha * loss_dir +
            self.beta * loss_mag +
            self.gamma * loss_cost +
            self.delta * loss_dd
        )

        # Components for logging
        loss_components = {
            'total': total_loss.item(),
            'directional': loss_dir.item(),
            'magnitude': loss_mag.item(),
            'trading_cost': loss_cost.item(),
            'drawdown': loss_dd.item()
        }

        return total_loss, loss_components


class AdaptiveCompositeLoss(nn.Module):
    """
    Composite loss with adaptive weighting.

    Automatically balances loss components based on their relative magnitudes.
    Useful when loss scales are unknown a priori.
    """

    def __init__(
        self,
        trading_fee: float = 0.001,
        max_acceptable_drawdown: float = 0.1,
        magnitude_temperature: float = 1.0,
        adaptation_rate: float = 0.1
    ):
        """
        Args:
            trading_fee: Transaction cost
            max_acceptable_drawdown: Maximum acceptable drawdown
            magnitude_temperature: Temperature for magnitude weighting
            adaptation_rate: Rate of weight adaptation (0 = no adaptation, 1 = full)
        """
        super().__init__()

        self.adaptation_rate = adaptation_rate

        # Component losses
        self.directional_loss = DirectionalLoss()
        self.magnitude_loss = MagnitudeWeightedMSE(temperature=magnitude_temperature)
        self.trading_cost_penalty = TradingCostPenalty(trading_fee=trading_fee)
        self.drawdown_penalty = DrawdownPenalty(max_acceptable_drawdown=max_acceptable_drawdown)

        # Learnable weights (initialized to 1.0)
        self.log_weights = nn.Parameter(torch.zeros(4))

        logger.info(f"Initialized AdaptiveCompositeLoss (adaptation_rate={adaptation_rate})")

    def forward(
        self,
        pred_return: torch.Tensor,
        true_return: torch.Tensor
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        """
        Args:
            pred_return: [batch] predicted returns
            true_return: [batch] actual returns

        Returns:
            total_loss: scalar
            loss_components: dict
        """
        batch_size = pred_return.shape[0]

        # Compute individual losses
        loss_dir = self.directional_loss(pred_return, true_return)
        loss_mag = self.magnitude_loss(pred_return, true_return)
        loss_cost = self.trading_cost_penalty(pred_return, batch_size)
        loss_dd = self.drawdown_penalty(pred_return, true_return)

        # Adaptive weights (softmax of learned log-weights)
        weights = torch.softmax(self.log_weights, dim=0)

        # Weighted combination
        losses = torch.stack([loss_dir, loss_mag, loss_cost, loss_dd])
        total_loss = (weights * losses).sum()

        # Components for logging
        loss_components = {
            'total': total_loss.item(),
            'directional': loss_dir.item(),
            'magnitude': loss_mag.item(),
            'trading_cost': loss_cost.item(),
            'drawdown': loss_dd.item(),
            'weights': weights.detach().cpu().numpy().tolist()
        }

        return total_loss, loss_components


if __name__ == "__main__":
    # Test losses
    logging.basicConfig(level=logging.INFO)

    batch_size = 100

    # Create fake data
    true_returns = torch.randn(batch_size) * 0.01  # ~1% returns
    pred_returns = true_returns + torch.randn(batch_size) * 0.005  # Noisy predictions

    print("\nTesting individual losses:")

    # Directional loss
    loss = DirectionalLoss()
    l = loss(pred_returns, true_returns)
    print(f"Directional loss: {l.item():.4f}")

    # Magnitude loss
    loss = MagnitudeWeightedMSE()
    l = loss(pred_returns, true_returns)
    print(f"Magnitude-weighted MSE: {l.item():.6f}")

    # Trading cost penalty
    loss = TradingCostPenalty()
    l = loss(pred_returns, batch_size)
    print(f"Trading cost penalty: {l.item():.6f}")

    # Drawdown penalty
    loss = DrawdownPenalty()
    l = loss(pred_returns, true_returns)
    print(f"Drawdown penalty: {l.item():.6f}")

    # Sharpe ratio loss
    loss = SharpeRatioLoss()
    l = loss(pred_returns, true_returns)
    print(f"Sharpe ratio loss: {l.item():.4f}")

    print("\nTesting composite loss:")
    loss = CompositeTradingLoss()
    total_loss, components = loss(pred_returns, true_returns)
    print(f"Total loss: {total_loss.item():.6f}")
    print(f"Components: {components}")

    print("\nTesting adaptive composite loss:")
    loss = AdaptiveCompositeLoss()
    total_loss, components = loss(pred_returns, true_returns)
    print(f"Total loss: {total_loss.item():.6f}")
    print(f"Components: {components}")

    print("\nLoss functions test passed!")
