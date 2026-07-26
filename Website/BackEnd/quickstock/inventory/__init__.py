from decimal import Decimal
import logging
from django.core.cache import cache

# Set up logger to catch unknown location codes during sales
logger = logging.getLogger(__name__)

def _get_tax_rate_for_location(location):
    """
    Returns the decimal tax rate based on the location's country_code.
    Defaults to 15% (Jamaica) but logs a warning if a new code is encountered.
    """
    CARIBBEAN_TAX_MAP = {
        "JM": Decimal("0.15"),   # Jamaica
        "BB": Decimal("0.175"),  # Barbados
        "TT": Decimal("0.125"),  # Trinidad & Tobago
        "GY": Decimal("0.14"),   # Guyana
        "LC": Decimal("0.125"),  # Saint Lucia
        "INT": Decimal("0.00"),  # International
    }
    
    if not location:
        return Decimal("0.15")
    
    # Extract code and standardize to match the dictionary keys
    country_code = getattr(location, "country_code", "JM").upper().strip()
    
    if country_code not in CARIBBEAN_TAX_MAP:
        logger.warning(f"Unknown tax code '{country_code}' for location {location.id}. Defaulting to 15%.")
        return Decimal("0.15")

    cached = cache.get(f"tax_rate:{country_code}")
    if cached is not None:
        try:
            return Decimal(str(cached))
        except Exception:
            logger.warning(f"Invalid cached tax rate '{cached}' for code '{country_code}'. Falling back to defaults.")

    return CARIBBEAN_TAX_MAP.get(country_code)
