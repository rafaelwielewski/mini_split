import hashlib
import json
from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from app.api.serializers import PaymentInputSerializer
from app.models import LedgerEntry, OutboxEvent, Payment
from app.services.split_calculator import calculate_payment


class PaymentView(APIView):
    def post(self, request):
        serializer = PaymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        idempotency_key = request.headers.get('Idempotency-Key')
        payload_hash = _hash_payload(data)

        if idempotency_key:
            existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                if existing.payload_hash == payload_hash:
                    return Response(_build_response(existing), status=status.HTTP_200_OK)
                return Response(
                    {'detail': 'Idempotency key already used with a different payload.'},
                    status=status.HTTP_409_CONFLICT,
                )

        calc = calculate_payment(
            amount=data['amount'],
            payment_method=data['payment_method'],
            installments=data['installments'],
            splits=data['splits'],
        )

        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    gross_amount=calc.gross_amount,
                    platform_fee_amount=calc.fee_amount,
                    net_amount=calc.net_amount,
                    payment_method=data['payment_method'],
                    installments=data['installments'],
                    currency=data['currency'],
                )
                LedgerEntry.objects.bulk_create(
                    [
                        LedgerEntry(
                            payment=payment,
                            recipient_id=r.recipient_id,
                            role=r.role,
                            amount=r.amount,
                        )
                        for r in calc.receivables
                    ]
                )
                OutboxEvent.objects.create(
                    payment=payment,
                    type='payment_captured',
                    payload={
                        'payment_id': str(payment.id),
                        'gross_amount': str(calc.gross_amount),
                        'net_amount': str(calc.net_amount),
                        'payment_method': data['payment_method'],
                        'installments': data['installments'],
                        'receivables': [
                            {'recipient_id': r.recipient_id, 'role': r.role, 'amount': str(r.amount)}
                            for r in calc.receivables
                        ],
                    },
                    status='pending',
                )
        except IntegrityError:
            # concurrent request with same key hit the unique constraint
            if not idempotency_key:
                raise

            payment = Payment.objects.get(idempotency_key=idempotency_key)
            if payment.payload_hash != payload_hash:
                return Response(
                    {'detail': 'Idempotency key already used with a different payload.'},
                    status=status.HTTP_409_CONFLICT,
                )

            return Response(_build_response(payment), status=status.HTTP_200_OK)

        return Response(_build_response(payment), status=status.HTTP_201_CREATED)


class QuoteView(APIView):
    def post(self, request):
        serializer = PaymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        calc = calculate_payment(
            amount=data['amount'],
            payment_method=data['payment_method'],
            installments=data['installments'],
            splits=data['splits'],
        )

        return Response(
            {
                'gross_amount': str(calc.gross_amount),
                'platform_fee_amount': str(calc.fee_amount),
                'net_amount': str(calc.net_amount),
                'receivables': [
                    {'recipient_id': r.recipient_id, 'role': r.role, 'amount': str(r.amount)} for r in calc.receivables
                ],
            }
        )


def _hash_payload(validated_data: dict) -> str:
    def serialize(obj):
        if isinstance(obj, Decimal):
            return str(obj.quantize(Decimal('0.01')))
        return str(obj)

    canonical = json.dumps(validated_data, sort_keys=True, ensure_ascii=False, default=serialize)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _build_response(payment: Payment) -> dict:
    entries = payment.ledger_entries.order_by('created_at')
    outbox = payment.outbox_events.order_by('created_at').first()
    return {
        'payment_id': f'pmt_{payment.id}',
        'status': payment.status,
        'gross_amount': str(payment.gross_amount),
        'platform_fee_amount': str(payment.platform_fee_amount),
        'net_amount': str(payment.net_amount),
        'receivables': [{'recipient_id': e.recipient_id, 'role': e.role, 'amount': str(e.amount)} for e in entries],
        'outbox_event': {'type': outbox.type, 'status': outbox.status} if outbox else None,
    }
