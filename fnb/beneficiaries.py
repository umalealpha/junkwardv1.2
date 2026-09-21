"""
fnb/beneficiaries.py

Sync ACTIVE `procurement.VendorBankAccount` rows to FNB as registered
beneficiaries. Two reasons:

  1. Some FNB APIs require the destination account to be pre-loaded as a
     beneficiary before any payment is allowed.
  2. We get FNB's account-name verification "for free" — if FNB rejects
     the beneficiary because the holder name doesn't match, our
     `name_mismatch` flag was already a fraud cue and now it's a hard fail.

STUB until the FNB spec arrives. The mapper is the single function you
update when Boitumelo's schema lands.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from .client import FNBClient, FNBNotConfigured
from .endpoints import BENEFICIARY_CREATE
from .models import FNBSyncLog


log = logging.getLogger(__name__)


def build_beneficiary_payload(vendor_bank_account) -> dict:
    """Map a VendorBankAccount to FNB's beneficiary shape."""
    vba = vendor_bank_account
    return {
        'beneficiary_reference': str(vba.pk),  # so FNB can callback us
        'vendor_name':           vba.contact.name,
        'account_holder_name':   vba.account_holder_name,
        'bank_name':             vba.bank_name,
        'account_number':        vba.account_number,
        'branch_code':           vba.branch_code,
        'branch_name':           vba.branch_name,
        'swift_bic':             vba.swift_bic,
        'iban':                  vba.iban,
        'currency':              vba.currency_code_id,
    }


def sync_beneficiary(vendor_bank_account, *, user: Optional[User] = None) -> dict:
    """Push a single ACTIVE bank account to FNB.

    Raises FNBNotConfigured until env vars are set. Once configured, posts
    to BENEFICIARY_CREATE and stores FNB's response in the sync log.
    """
    from procurement.models import VendorBankAccount

    if vendor_bank_account.status != VendorBankAccount.Status.ACTIVE:
        raise ValidationError(
            f'Only ACTIVE bank accounts may be synced. Current: '
            f'{vendor_bank_account.status}.'
        )

    payload = build_beneficiary_payload(vendor_bank_account)

    client = FNBClient(user=user)
    resp = client.post(
        BENEFICIARY_CREATE,
        service        = FNBSyncLog.Service.BENEFICIARY,
        json_body      = payload,
        request_summary= (
            f'Sync beneficiary {vendor_bank_account.contact.name} '
            f'({vendor_bank_account.bank_name})'
        ),
    )

    return {
        'fnb_reference': resp.json.get('reference')
                         or resp.json.get('id') or '',
        'status':        resp.json.get('status', 'unknown'),
        'verified':      bool(resp.json.get('verified', False)),
    }


def sync_all_active_beneficiaries(*, user: Optional[User] = None) -> dict:
    """One-shot bulk sync of every ACTIVE vendor bank account."""
    from procurement.models import VendorBankAccount

    qs = VendorBankAccount.objects.filter(
        status=VendorBankAccount.Status.ACTIVE,
    )

    successes = 0
    failures  = []
    for vba in qs:
        try:
            sync_beneficiary(vba, user=user)
            successes += 1
        except FNBNotConfigured:
            return {
                'status':     'not_configured',
                'successes':  0,
                'failures':   [],
                'total':      qs.count(),
            }
        except Exception as e:  # noqa: BLE001
            failures.append({
                'vendor':       vba.contact.name,
                'bank_account': str(vba.pk),
                'error':        str(e)[:200],
            })

    return {
        'status':     'ok',
        'successes':  successes,
        'failures':   failures,
        'total':      qs.count(),
    }
