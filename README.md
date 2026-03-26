# cakto-split

Mini split engine para o desafio técnico da Cakto. Calcula taxas, distribui recebíveis entre múltiplos destinatários e registra o pagamento com ledger entries e um evento de outbox.

## Stack

- Python 3.12
- Django 5.1 + Django REST Framework
- SQLite (dev) — troque pela variável `DATABASE_URL` em produção
- pytest

## Como rodar

```bash
# Instalar dependências
make install

# Criar o banco
make migrate

# Subir o servidor
make dev
```

## Endpoints

### POST /api/v1/payments

Confirma um pagamento. Aceita o header `Idempotency-Key`.

**Request:**
```json
{
  "amount": "297.00",
  "currency": "BRL",
  "payment_method": "card",
  "installments": 3,
  "splits": [
    { "recipient_id": "producer_1", "role": "producer", "percent": 70 },
    { "recipient_id": "affiliate_9", "role": "affiliate", "percent": 30 }
  ]
}
```

**Response (201):**
```json
{
  "payment_id": "pmt_a1b2c3d4-...",
  "status": "captured",
  "gross_amount": "297.00",
  "platform_fee_amount": "26.70",
  "net_amount": "270.30",
  "receivables": [
    { "recipient_id": "producer_1", "role": "producer", "amount": "189.21" },
    { "recipient_id": "affiliate_9", "role": "affiliate", "amount": "81.09" }
  ],
  "outbox_event": { "type": "payment_captured", "status": "pending" }
}
```

Idempotência via header `Idempotency-Key`: mesma key + mesmo payload retorna 200 com o resultado original; mesma key + payload diferente retorna 409. Sem o header, o endpoint processa normalmente sem proteção contra duplicatas.

### POST /api/v1/checkout/quote

Simula o cálculo sem persistir nada. Mesma request do `/payments`, sem o header de idempotência. Útil para mostrar o breakdown de taxas antes de confirmar.

## Testes

```bash
make test
```

Cobre os 5 cenários do desafio: PIX com taxa zero, CARD 3x split 70/30, arredondamento de centavo, idempotência (mesma key sem duplicata, key diferente com 409), além de validações de input e o comportamento do quote.

## Decisões técnicas

### Precisão

Tudo `Decimal`, do serializer até o banco. Nunca passa por `float`. A taxa é calculada com `ROUND_HALF_UP` sobre o gross amount — então 297 × 8.99% = 26.7003 vira 26.70.

### Distribuição de centavos

Usei largest remainder: calculo o `floor` de cada parte e distribuo os centavos restantes para quem tem maior fração descartada. Garante que `sum(receivables) == net_amount` exatamente. Arredondar cada parcela de forma independente seria mais simples, mas pode gerar ±1 centavo de divergência no total — num ledger isso não é aceitável.

### Idempotência

`Idempotency-Key` no header é opcional. Quando presente, o payload é serializado em JSON canônico (chaves ordenadas, `Decimal` normalizado para dois casas) e transformado em SHA-256. O hash fica gravado no Payment.

- Mesma key + mesmo hash → 200 com o resultado original, sem side effects
- Mesma key + hash diferente → 409, o cliente reutilizou a key com outro payload
- Race condition de requests simultâneas → o `unique=True` do banco lança `IntegrityError`, capturado e tratado retornando 200

### Em produção monitoraria

- Latência p99 do `POST /payments`
- Taxa de 409 por `Idempotency-Key` — se subir, tem bug no cliente
- Lag da fila de outbox (`status = 'pending'` com `created_at` antigo)
- Job de reconciliação periódico: `sum(ledger_entries.amount) != payment.net_amount` não deveria acontecer nunca, mas precisa de alerta

### O que faria a seguir

Worker pra publicar os `OutboxEvent` pendentes, `DATABASE_URL` via `dj-database-url` pra trocar de SQLite em prod, e um `GET /api/v1/payments/{id}` pra consulta.

## Uso de IA

Usei IA como apoio para levantar casos de borda no algoritmo de distribuição de centavos e sugerir métricas de monitoramento. A arquitetura do projeto, a estratégia de idempotência e a implementação do split engine foram definidas e revisadas por mim. E por fim na utilização de code-review no Pull Request.

## PR

[feature/payment-split-ledger → main](https://github.com/rafaelwielewski/mini_split/pull/1)
