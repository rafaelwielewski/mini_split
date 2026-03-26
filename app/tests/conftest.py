import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def pix_payload():
    return {
        'amount': '100.00',
        'currency': 'BRL',
        'payment_method': 'pix',
        'splits': [
            {'recipient_id': 'producer_1', 'role': 'producer', 'percent': 100},
        ],
    }


@pytest.fixture
def card_payload():
    return {
        'amount': '297.00',
        'currency': 'BRL',
        'payment_method': 'card',
        'installments': 3,
        'splits': [
            {'recipient_id': 'producer_1', 'role': 'producer', 'percent': 70},
            {'recipient_id': 'affiliate_9', 'role': 'affiliate', 'percent': 30},
        ],
    }
