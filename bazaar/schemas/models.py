"""Core Bazaar data model.

Design rules
------------
* Money is always integer **paise** (``*_paise``). Never floats.
* Fields under the ``india`` namespace mirror the ``bazaar.india`` protocol extension;
  everything else maps 1:1 onto Schema.org ``Product``/``Offer`` and UCP/ACP catalog fields.
* Free-text fields (name, description, synonyms, highlights) are *untrusted data*: they are
  rendered to LLMs as data, never as instructions. See ``bazaar.compiler.sanitize``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# --------------------------------------------------------------------------- enums


class Unit(str, Enum):
    PIECE = "pc"
    KG = "kg"
    GRAM = "g"
    LITRE = "l"
    ML = "ml"
    DOZEN = "dozen"
    PACK = "pack"
    METRE = "m"
    BOX = "box"


class Vertical(str, Enum):
    GROCERY = "grocery"
    APPAREL = "apparel"
    ELECTRONICS = "electronics_accessories"
    HOME_KITCHEN = "home_kitchen"
    B2B_PACKAGING = "b2b_packaging"


class Segment(str, Enum):
    """Merchant-defined *business* segments. Never demographic."""

    ANY = "any"
    NEW = "new"
    RETURNING = "returning"
    B2B = "b2b"


class OfferType(str, Enum):
    PERCENT = "percent"
    FLAT = "flat"
    FREE_DELIVERY = "free_delivery"


class AgentTier(int, Enum):
    T0_UNSIGNED = 0  # browse + quote; payment only via embedded Razorpay Checkout
    T1_SIGNED = 1  # + reserve + negotiate
    T2_VERIFIED = 2  # + direct checkout with a Scoped Payment Grant
    T3_VETTED = 3  # + higher limits


# --------------------------------------------------------------------------- catalog


class FieldConfidence(BaseModel):
    """Per-field confidence produced by the compiler; anything < ``review_threshold`` is queued."""

    name: float = 1.0
    price: float = 1.0
    unit: float = 1.0
    category: float = 1.0
    gst: float = 1.0
    stock: float = 1.0

    def min(self) -> float:
        return min(self.name, self.price, self.unit, self.category, self.gst, self.stock)


class Variant(BaseModel):
    variant_id: str
    label: str  # e.g. "M / Blue", "500 g"
    price_paise: int = Field(ge=0)
    stock: int = Field(ge=0)


class Product(BaseModel):
    sku: str
    name: str  # canonical, buyer-facing (English)
    source_name: str = ""  # exactly as the merchant wrote it (untrusted)
    description: str = ""
    category: str
    unit: Unit = Unit.PIECE
    pack_size: float = 1.0  # e.g. 5 (kg), 500 (g)
    price_paise: int = Field(ge=0)
    stock: int = Field(ge=0)
    variants: list[Variant] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)  # Hindi/Hinglish/regional names
    use_case_tags: list[str] = Field(default_factory=list)
    buyer_highlights: list[str] = Field(default_factory=list)
    # bazaar.india extension
    hsn: str = ""
    gst_rate_bp: int = Field(default=0, ge=0, le=2800)  # basis points: 500 = 5 %
    cod_allowed: bool = True
    lead_time_hours: int = Field(default=24, ge=0)
    # compiler metadata
    confidence: FieldConfidence = Field(default_factory=FieldConfidence)
    flags: list[str] = Field(default_factory=list)  # e.g. "instruction_like_text_stripped"

    @field_validator("sku")
    @classmethod
    def _sku_shape(cls, v: str) -> str:
        if not v or " " in v:
            raise ValueError("sku must be non-empty and contain no spaces")
        return v

    def unit_price_paise(self) -> int:
        """Price per single unit (for per-kg/per-pc quoting)."""
        return self.price_paise

    def gst_paise(self, subtotal_paise: int) -> int:
        return (subtotal_paise * self.gst_rate_bp + 5000) // 10000


# --------------------------------------------------------------------------- merchant policy


class OfferRule(BaseModel):
    """A pre-approved offer. The LLM may only *select* a rule id; it can never invent one."""

    rule_id: str
    version: int = 1
    type: OfferType
    value: int = Field(ge=0)  # percent (0-100) for PERCENT, paise for FLAT, ignored for FREE_DELIVERY
    min_cart_paise: int = Field(default=0, ge=0)
    min_qty: int = Field(default=0, ge=0)
    segment: Segment = Segment.ANY
    max_discount_paise: int = Field(default=0, ge=0)  # 0 = no cap
    stackable: bool = False
    valid_until: datetime | None = None
    description: str = ""

    @model_validator(mode="after")
    def _bounds(self) -> OfferRule:
        if self.type == OfferType.PERCENT and self.value > 100:
            raise ValueError("percent offer cannot exceed 100")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        if self.valid_until is None:
            return True
        now = now or datetime.now(timezone.utc)
        return now <= self.valid_until


class Serviceability(BaseModel):
    """Where and how the merchant delivers. Pincode prefixes are inclusive (``"5600"`` covers 560001-560099)."""

    pincode_prefixes: list[str] = Field(default_factory=list)
    pincodes: list[str] = Field(default_factory=list)
    delivery_fee_paise: int = Field(default=0, ge=0)
    free_delivery_above_paise: int = Field(default=0, ge=0)  # 0 = never free
    eta_hours: int = Field(default=24, ge=0)
    cod_allowed: bool = True

    def serves(self, pincode: str) -> bool:
        if not pincode or not pincode.isdigit() or len(pincode) != 6:
            return False
        if pincode in self.pincodes:
            return True
        return any(pincode.startswith(p) for p in self.pincode_prefixes)

    def fee_for(self, subtotal_paise: int) -> int:
        if self.free_delivery_above_paise and subtotal_paise >= self.free_delivery_above_paise:
            return 0
        return self.delivery_fee_paise


class MerchantPolicy(BaseModel):
    review_first: bool = False  # merchant approves every checkout before payment link is issued
    kill_switch: bool = False  # one-tap disable; agent refuses everything
    agent_allowlist: list[str] = Field(default_factory=list)  # keyids; empty = tier rules only
    min_tier_for_checkout: AgentTier = AgentTier.T2_VERIFIED
    max_negotiation_rounds: int = Field(default=2, ge=0, le=5)
    max_order_paise: int = Field(default=50_000_00, ge=0)  # ₹50,000 default cap per agent order
    refunds_per_hour: int = Field(default=5, ge=0)
    allowed_languages: list[str] = Field(default_factory=lambda: ["en", "hi"])


class Merchant(BaseModel):
    merchant_id: str
    name: str
    vertical: Vertical
    city: str
    base_pincode: str
    languages: list[str] = Field(default_factory=lambda: ["en", "hi"])
    gstin: str = ""
    serviceability: Serviceability = Field(default_factory=Serviceability)
    offer_rules: list[OfferRule] = Field(default_factory=list)
    policy: MerchantPolicy = Field(default_factory=MerchantPolicy)
    products: list[Product] = Field(default_factory=list)
    source_kind: Literal["csv", "sheet", "shopify", "woocommerce", "image", "instagram", "voice"] = "csv"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def product(self, sku: str) -> Product | None:
        return next((p for p in self.products if p.sku == sku), None)

    def rule(self, rule_id: str) -> OfferRule | None:
        return next((r for r in self.offer_rules if r.rule_id == rule_id), None)
