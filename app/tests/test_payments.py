from decimal import Decimal

import pytest

from app.models import Payment
from app.services.split_calculator import Receivable, calculate_fee_rate, calculate_payment


def test_pix_fee_rate_is_zero():
    assert calculate_fee_rate('pix', 1) == Decimal('0')


def test_card_1x_fee_rate():
    assert calculate_fee_rate('card', 1) == Decimal('0.0399')


def test_card_3x_fee_rate():
    # 4.99% + 2% * (3-1) = 8.99%
    assert calculate_fee_rate('card', 3) == Decimal('0.0899')


def test_card_12x_fee_rate():
    # 4.99% + 2% * (12-1) = 26.99%
    assert calculate_fee_rate('card', 12) == Decimal('0.2699')


def test_pix_zero_fee():
    result = calculate_payment(
        amount=Decimal('100.00'),
        payment_method='pix',
        installments=1,
        splits=[{'recipient_id': 'r1', 'role': 'producer', 'percent': 100}],
    )
    assert result.fee_amount == Decimal('0.00')
    assert result.net_amount == Decimal('100.00')
    assert result.gross_amount == Decimal('100.00')
    assert result.receivables[0].amount == Decimal('100.00')


def test_card_3x_split_70_30():
    result = calculate_payment(
        amount=Decimal('297.00'),
        payment_method='card',
        installments=3,
        splits=[
            {'recipient_id': 'producer_1', 'role': 'producer', 'percent': 70},
            {'recipient_id': 'affiliate_9', 'role': 'affiliate', 'percent': 30},
        ],
    )
    # fee = 297 * 8.99% = 26.7003 → rounds to 26.70
    assert result.fee_amount == Decimal('26.70')
    assert result.net_amount == Decimal('270.30')
    total = sum(r.amount for r in result.receivables)
    assert total == Decimal('270.30')


def test_rounding_cent_distribution():
    # 100.01 split 33/33/34 — largest remainder ensures sum == net
    result = calculate_payment(
        amount=Decimal('100.01'),
        payment_method='pix',
        installments=1,
        splits=[
            {'recipient_id': 'r1', 'role': 'producer', 'percent': 33},
            {'recipient_id': 'r2', 'role': 'affiliate', 'percent': 33},
            {'recipient_id': 'r3', 'role': 'affiliate', 'percent': 34},
        ],
    )
    total = sum(r.amount for r in result.receivables)
    assert total == result.net_amount


def test_receivable_is_correct_type():
    result = calculate_payment(
        amount=Decimal('50.00'),
        payment_method='pix',
        installments=1,
        splits=[{'recipient_id': 'r1', 'role': 'producer', 'percent': 100}],
    )
    assert isinstance(result.receivables[0], Receivable)
    assert result.receivables[0].recipient_id == 'r1'
    assert result.receivables[0].role == 'producer'


# --- Integration tests (require DB) ---

PAYMENTS_URL = '/api/v1/payments'
QUOTE_URL = '/api/v1/checkout/quote'


@pytest.mark.django_db
def test_api_pix_zero_fee_full_split(api_client, pix_payload):
    response = api_client.post(PAYMENTS_URL, pix_payload, format='json')

    assert response.status_code == 201
    data = response.json()
    assert Decimal(data['platform_fee_amount']) == Decimal('0.00')
    assert Decimal(data['net_amount']) == Decimal('100.00')
    assert len(data['receivables']) == 1
    assert Decimal(data['receivables'][0]['amount']) == Decimal('100.00')
    assert data['outbox_event']['type'] == 'payment_captured'
    assert data['outbox_event']['status'] == 'pending'


@pytest.mark.django_db
def test_api_card_3x_split_70_30(api_client, card_payload):
    response = api_client.post(PAYMENTS_URL, card_payload, format='json')

    assert response.status_code == 201
    data = response.json()
    assert Decimal(data['platform_fee_amount']) == Decimal('26.70')
    assert Decimal(data['net_amount']) == Decimal('270.30')
    total = sum(Decimal(r['amount']) for r in data['receivables'])
    assert total == Decimal('270.30')


@pytest.mark.django_db
def test_api_rounding_sum_equals_net(api_client):
    payload = {
        'amount': '100.01',
        'currency': 'BRL',
        'payment_method': 'pix',
        'splits': [
            {'recipient_id': 'r1', 'role': 'producer', 'percent': 33},
            {'recipient_id': 'r2', 'role': 'affiliate', 'percent': 33},
            {'recipient_id': 'r3', 'role': 'affiliate', 'percent': 34},
        ],
    }
    response = api_client.post(PAYMENTS_URL, payload, format='json')

    assert response.status_code == 201
    data = response.json()
    net = Decimal(data['net_amount'])
    total = sum(Decimal(r['amount']) for r in data['receivables'])
    assert total == net


@pytest.mark.django_db
def test_idempotency_same_key_no_duplicate(api_client, card_payload):
    headers = {'HTTP_IDEMPOTENCY_KEY': 'key-abc-123'}

    response1 = api_client.post(PAYMENTS_URL, card_payload, format='json', **headers)
    response2 = api_client.post(PAYMENTS_URL, card_payload, format='json', **headers)

    assert response1.status_code == 201
    assert response2.status_code == 200
    assert response1.json()['payment_id'] == response2.json()['payment_id']
    assert Payment.objects.filter(idempotency_key='key-abc-123').count() == 1


@pytest.mark.django_db
def test_idempotency_different_payload_returns_409(api_client, card_payload):
    headers = {'HTTP_IDEMPOTENCY_KEY': 'key-conflict-456'}

    different_payload = {**card_payload, 'amount': '500.00'}

    response1 = api_client.post(PAYMENTS_URL, card_payload, format='json', **headers)
    response2 = api_client.post(PAYMENTS_URL, different_payload, format='json', **headers)

    assert response1.status_code == 201
    assert response2.status_code == 409


@pytest.mark.django_db
def test_idempotency_race_different_payload_returns_409(api_client, card_payload, monkeypatch):
    class _AlwaysEmptyQuerySet:
        @staticmethod
        def first():
            return None

    # Force the pre-check to miss existing payments so second request hits
    # the IntegrityError path and validates the recovered payment hash.
    monkeypatch.setattr(Payment.objects, 'filter', lambda *args, **kwargs: _AlwaysEmptyQuerySet())

    headers = {'HTTP_IDEMPOTENCY_KEY': 'key-race-409-999'}
    different_payload = {**card_payload, 'amount': '500.00'}

    response1 = api_client.post(PAYMENTS_URL, card_payload, format='json', **headers)
    response2 = api_client.post(PAYMENTS_URL, different_payload, format='json', **headers)

    assert response1.status_code == 201
    assert response2.status_code == 409


@pytest.mark.django_db
def test_pix_with_installments_returns_400(api_client):
    payload = {
        'amount': '100.00',
        'currency': 'BRL',
        'payment_method': 'pix',
        'installments': 3,
        'splits': [{'recipient_id': 'r1', 'role': 'producer', 'percent': 100}],
    }
    response = api_client.post(PAYMENTS_URL, payload, format='json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_splits_percent_not_100_returns_400(api_client):
    payload = {
        'amount': '100.00',
        'currency': 'BRL',
        'payment_method': 'pix',
        'splits': [
            {'recipient_id': 'r1', 'role': 'producer', 'percent': 70},
            {'recipient_id': 'r2', 'role': 'affiliate', 'percent': 20},
        ],
    }
    response = api_client.post(PAYMENTS_URL, payload, format='json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_quote_does_not_persist(api_client, card_payload):
    response = api_client.post(QUOTE_URL, card_payload, format='json')

    assert response.status_code == 200
    assert Payment.objects.count() == 0
    data = response.json()
    assert 'net_amount' in data
    assert 'receivables' in data
    assert 'payment_id' not in data


@pytest.mark.django_db
def test_amount_zero_returns_400(api_client):
    payload = {
        'amount': '0.00',
        'currency': 'BRL',
        'payment_method': 'pix',
        'splits': [{'recipient_id': 'r1', 'role': 'producer', 'percent': 100}],
    }
    response = api_client.post(PAYMENTS_URL, payload, format='json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_card_installments_out_of_range_returns_400(api_client):
    payload = {
        'amount': '100.00',
        'currency': 'BRL',
        'payment_method': 'card',
        'installments': 13,
        'splits': [{'recipient_id': 'r1', 'role': 'producer', 'percent': 100}],
    }
    response = api_client.post(PAYMENTS_URL, payload, format='json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_more_than_5_splits_returns_400(api_client):
    payload = {
        'amount': '100.00',
        'currency': 'BRL',
        'payment_method': 'pix',
        'splits': [{'recipient_id': f'r{i}', 'role': 'affiliate', 'percent': 16} for i in range(6)],
    }
    response = api_client.post(PAYMENTS_URL, payload, format='json')
    assert response.status_code == 400
