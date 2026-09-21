"""
assets/component_models.py

Fixed-asset componentisation helpers.

IFRS recognises that a single physical asset can be a composite of
components with different useful lives — e.g. a building's roof, HVAC,
and structural shell each depreciate independently. Alpha Direct
implements this with a parent/child self-FK on Asset:

    Parent Asset (the "shell")
      ├── Component Asset A (own cost, life, depreciation schedule)
      ├── Component Asset B
      └── ...

The schema-side change is a single field on Asset:
    parent_asset = ForeignKey('self', null=True, blank=True,
                              related_name='child_components')

…and a convenience property:
    Asset.components → QuerySet of children (= self.child_components.all())

This module:
  • Documents the design (above).
  • Exposes `walk_total_monthly_depreciation(asset)` — the function used
    by assets/services.py to compute the parent's effective monthly
    depreciation as parent_own + Σ child.monthly_depreciation_amount().

The field itself is declared inline on Asset in assets/models.py because
Django requires field declarations on the model class itself. This module
is imported by models.py to register the depreciation-walking helper.

Behaviour stays IDENTICAL when an asset has no children — the helper
short-circuits to `asset.monthly_depreciation_amount()`.
"""

from __future__ import annotations

from decimal import Decimal


ZERO = Decimal('0.00')


def walk_total_monthly_depreciation(asset) -> Decimal:
    """
    Total monthly depreciation for `asset` INCLUDING its component children.

    Composite behaviour:
      • Parent's own depreciable amount → parent's own monthly charge.
      • Each child's depreciable amount → child's own monthly charge.
      • Sum them, clamped at zero.

    When the asset has no children (the common case today), this returns
    exactly what `asset.monthly_depreciation_amount()` returns — so the
    monthly depreciation run is unchanged for non-componentised assets.

    Note: child Assets are themselves rows in the assets register; they
    each post their own DepreciationEntry + JournalEntry through the
    standard depreciate_asset() path. This helper is the SUMMING view
    used for parent-level reporting and for the bulk depreciation
    iterator (which walks every Asset regardless and skips parent-
    aggregations to avoid double-counting).
    """
    own = asset.monthly_depreciation_amount()

    # Cheap branch — avoid the related-manager hit when no children exist.
    # The `components` property is added to Asset in models.py and resolves
    # to `self.child_components.all()`. Using getattr keeps this safe if
    # the property hasn't been wired yet during early model import.
    children = getattr(asset, 'components', None)
    if children is None:
        return own

    total = own
    try:
        child_iter = children.iterator() if hasattr(children, 'iterator') else iter(children)
    except Exception:  # pragma: no cover — defensive
        return own

    for child in child_iter:
        total += child.monthly_depreciation_amount() or ZERO

    return total if total > ZERO else ZERO
