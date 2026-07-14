#!/usr/bin/env python3
"""Popula a tabela DynamoDB de produtos com dados de exemplo.

Uso:
    pip install boto3
    export AWS_DEFAULT_REGION=us-west-2
    python scripts/seed_products.py                 # tabela "Products"
    python scripts/seed_products.py MinhaTabela     # tabela customizada

Requer credenciais AWS com permissão dynamodb:PutItem na tabela alvo.
A tabela deve existir previamente (criada pela stack em iac/template.yaml).
"""
import sys
from decimal import Decimal

import boto3

PRODUCTS = [
    {
        "id": "1",
        "name": "Fone de Ouvido Bluetooth",
        "description": "Fone sem fio com cancelamento de ruído e 30h de bateria.",
        "category": "Eletrônicos",
        "stock": 42,
        "price": Decimal("299.90"),
    },
    {
        "id": "2",
        "name": "Teclado Mecânico RGB",
        "description": "Switches azuis, iluminação RGB e apoio de pulso.",
        "category": "Periféricos",
        "stock": 18,
        "price": Decimal("459.00"),
    },
    {
        "id": "3",
        "name": "Mouse Gamer 16000 DPI",
        "description": "Sensor óptico de alta precisão com 8 botões programáveis.",
        "category": "Periféricos",
        "stock": 65,
        "price": Decimal("189.90"),
    },
    {
        "id": "4",
        "name": "Monitor 27\" 144Hz",
        "description": "Painel IPS Quad HD com 144Hz e 1ms de resposta.",
        "category": "Monitores",
        "stock": 12,
        "price": Decimal("1599.00"),
    },
    {
        "id": "5",
        "name": "Webcam Full HD",
        "description": "Webcam 1080p com microfone estéreo e foco automático.",
        "category": "Eletrônicos",
        "stock": 30,
        "price": Decimal("249.90"),
    },
    {
        "id": "6",
        "name": "Cadeira Ergonômica",
        "description": "Cadeira de escritório com apoio lombar ajustável.",
        "category": "Mobiliário",
        "stock": 8,
        "price": Decimal("1199.00"),
    },
]


def main():
    table_name = sys.argv[1] if len(sys.argv) > 1 else "Products"
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    with table.batch_writer() as batch:
        for product in PRODUCTS:
            batch.put_item(Item=product)
            print(f"  + {product['id']}: {product['name']}")

    print(f"\n{len(PRODUCTS)} produtos inseridos na tabela '{table_name}'.")


if __name__ == "__main__":
    main()
