from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


@dataclass
class Receivable:
    recipient_id: str
    role: str
    amount: Decimal


@dataclass
class PaymentCalculation:
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    receivables: list[Receivable]


def calculate_fee_rate(payment_method: str, installments: int) -> Decimal:
    if payment_method == 'pix':
        return Decimal('0')
    return Decimal('0.0399') if installments == 1 else Decimal('0.0499') + Decimal('0.02') * (installments - 1)


def calculate_payment(
    amount: Decimal,
    payment_method: str,
    installments: int,
    splits: list[dict],
) -> PaymentCalculation:
    fee_rate = calculate_fee_rate(payment_method, installments)
    fee_amount = (amount * fee_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    net_amount = amount - fee_amount

    return PaymentCalculation(
        gross_amount=amount,
        fee_amount=fee_amount,
        net_amount=net_amount,
        receivables=_distribute_split(net_amount, splits),
    )


def _distribute_split(net: Decimal, splits: list[dict]) -> list[Receivable]:
    items = []
    for split in splits:
        percent = Decimal(str(split['percent']))
        raw = net * percent / Decimal('100')
        floor_amount = raw.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        items.append(
            {
                'recipient_id': split['recipient_id'],
                'role': split['role'],
                'amount': floor_amount,
                'remainder': raw - floor_amount,
            }
        )

    distributed = sum(item['amount'] for item in items)
    leftover_cents = int(((net - distributed) * 100).quantize(Decimal('1')))

    items.sort(key=lambda x: x['remainder'], reverse=True)
    for i in range(leftover_cents):
        items[i]['amount'] += Decimal('0.01')

    return [Receivable(recipient_id=item['recipient_id'], role=item['role'], amount=item['amount']) for item in items]
