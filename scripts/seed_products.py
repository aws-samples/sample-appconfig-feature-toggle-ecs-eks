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
        "name": "Bluetooth Headphones",
        "description": "Wireless headphones with noise cancelling and 30h battery life.",
        "category": "Electronics",
        "stock": 42,
        "price": Decimal("299.90"),
    },
    {
        "id": "2",
        "name": "RGB Mechanical Keyboard",
        "description": "Blue switches, RGB backlight and wrist rest.",
        "category": "Peripherals",
        "stock": 18,
        "price": Decimal("459.00"),
    },
    {
        "id": "3",
        "name": "Gaming Mouse 16000 DPI",
        "description": "High-precision optical sensor with 8 programmable buttons.",
        "category": "Peripherals",
        "stock": 65,
        "price": Decimal("189.90"),
    },
    {
        "id": "4",
        "name": "27\" 144Hz Monitor",
        "description": "Quad HD IPS panel with 144Hz refresh rate and 1ms response.",
        "category": "Monitors",
        "stock": 12,
        "price": Decimal("1599.00"),
    },
    {
        "id": "5",
        "name": "Full HD Webcam",
        "description": "1080p webcam with stereo microphone and autofocus.",
        "category": "Electronics",
        "stock": 30,
        "price": Decimal("249.90"),
    },
    {
        "id": "6",
        "name": "Ergonomic Chair",
        "description": "Office chair with adjustable lumbar support.",
        "category": "Furniture",
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

    print(f"\n{len(PRODUCTS)} products inserted into table '{table_name}'.")


if __name__ == "__main__":
    main()
