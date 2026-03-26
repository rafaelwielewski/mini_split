from decimal import Decimal

from rest_framework import serializers


class SplitSerializer(serializers.Serializer):
    recipient_id = serializers.CharField(max_length=255)
    role = serializers.CharField(max_length=50)
    percent = serializers.IntegerField(min_value=1, max_value=100)


class PaymentInputSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    currency = serializers.ChoiceField(choices=['BRL'])
    payment_method = serializers.ChoiceField(choices=['pix', 'card'])
    installments = serializers.IntegerField(min_value=1, max_value=12, default=1)
    splits = SplitSerializer(many=True)

    def validate(self, data):
        method = data['payment_method']
        installments = data.get('installments', 1)
        splits = data['splits']

        if method == 'pix' and installments != 1:
            raise serializers.ValidationError({'installments': 'PIX does not support installments.'})

        if not (1 <= len(splits) <= 5):
            raise serializers.ValidationError({'splits': 'Must have between 1 and 5 recipients.'})

        total_percent = sum(s['percent'] for s in splits)
        if total_percent != 100:
            raise serializers.ValidationError({'splits': f'Percent must sum to 100, got {total_percent}.'})

        return data
