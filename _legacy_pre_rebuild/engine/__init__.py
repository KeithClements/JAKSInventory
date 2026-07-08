"""Engine domain package.

Keep engine parsing/normalization logic here so scraper/UI/uploader can share it.
"""

from .product_engine import EngineInfo, ProductEngine, ProductEngineConfig
from .product_validation import ProductValidation, validate_product
from .template_inference import infer_product_type, infer_template_suffix

__all__ = [
    "EngineInfo",
    "ProductEngine",
    "ProductEngineConfig",
    "ProductValidation",
    "validate_product",
    "infer_product_type",
    "infer_template_suffix",
]
