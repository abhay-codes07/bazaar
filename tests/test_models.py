import pytest
from pydantic import ValidationError

from bazaar.schemas.models import OfferRule, OfferType, Product, Serviceability, Unit


def test_product_gst_rounding():
    p = Product(sku="a-1", name="Rice", category="staples", unit=Unit.KG, price_paise=11000, stock=5, gst_rate_bp=500)
    assert p.gst_paise(11000) == 550
    assert p.gst_paise(1) == 0  # rounds half-up at paise level
    assert p.gst_paise(101) == 5


def test_product_rejects_bad_sku():
    with pytest.raises(ValidationError):
        Product(sku="has space", name="x", category="c", price_paise=1, stock=0)


def test_offer_rule_percent_bound():
    with pytest.raises(ValidationError):
        OfferRule(rule_id="X", type=OfferType.PERCENT, value=120)


def test_serviceability_prefix_and_exact():
    s = Serviceability(pincode_prefixes=["5600"], pincodes=["411045"], delivery_fee_paise=4900, free_delivery_above_paise=99900)
    assert s.serves("560034")
    assert s.serves("411045")
    assert not s.serves("411046")
    assert not s.serves("56003")  # not 6 digits
    assert not s.serves("abcdef")
    assert s.fee_for(50000) == 4900
    assert s.fee_for(99900) == 0
