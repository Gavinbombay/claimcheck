"""claimcheck: check an agent's claimed actions against evidence."""
from .core import Claim, Result, check_claim, check_all, load_claims

__all__ = ["Claim", "Result", "check_claim", "check_all", "load_claims"]
__version__ = "0.1.0"
