from flask import Flask, jsonify
import boto3
import json
import os
import logging
from flask_cors import CORS
from decimal import Decimal
import requests



# Configurar região AWS
app = Flask(__name__)
CORS(app)

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Configuração lida de variáveis de ambiente (injetadas pelo deployment ECS/EKS).
# Os valores default facilitam o teste local, mas em produção use os IDs reais
# criados pela stack de AppConfig (ver template.yaml).
APPCONFIG_APPLICATION_NAME = os.environ.get("APPCONFIG_APP_ID", "MyPythonApp")
APPCONFIG_CONFIG_PROFILE_NAME = os.environ.get("APPCONFIG_CONFIG_ID", "FeatureFlags")
APPCONFIG_ENVIRONMENT_NAME = os.environ.get("APPCONFIG_ENV_ID", "Demo")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
APPCONFIG_AGENT_BASE_URL = os.environ.get("APPCONFIG_AGENT_BASE_URL", "http://localhost:2772")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "Products")
flag_key = "discount_enabled"

# Função auxiliar para converter do formato Decimal do DynamoDB para float
def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError("Object of type '%s' is not JSON serializable" % type(obj).__name__)


def get_config(flag_key=None):
    """Lê a configuração diretamente do AppConfig Agent local.

    Sem cache em nível de aplicação: o próprio agent sidecar já mantém um cache
    local e faz o polling do AppConfig, e as respostas via localhost retornam em
    micro/milissegundos. Consultar o agent a cada request entrega sempre o valor
    mais fresco e evita bugs de lógica de cache no código da aplicação.
    A degradação graciosa (fallback seguro) fica em get_discount_feature().
    """
    # Constrói a URL base
    url = f"{APPCONFIG_AGENT_BASE_URL}/applications/{APPCONFIG_APPLICATION_NAME}/environments/{APPCONFIG_ENVIRONMENT_NAME}/configurations/{APPCONFIG_CONFIG_PROFILE_NAME}"

    # Adiciona o parâmetro flag se especificado
    if flag_key:
        url += f"?flag={flag_key}"

    # Faz a requisição para o AppConfig Agent (timeout curto: é uma chamada local)
    response = requests.get(url, timeout=5)
    response.raise_for_status()  # Levanta exceção para status codes de erro

    return json.loads(response.content.decode('utf-8'))

def get_products():
    """Consulta todos os produtos no DynamoDB"""
    logger.info("Iniciando busca de produtos no DynamoDB")
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        
        logger.info("Executando scan na tabela Products")
        response = table.scan()
        products = response.get('Items', [])
        
        logger.info(f"Encontrados {len(products)} produtos no DynamoDB")
        if products:
            logger.info(f"Primeiro produto encontrado: {json.dumps(products[0], default=decimal_default)}")
        
        return products
    except Exception as e:
        logger.error(f"Erro ao buscar produtos: {str(e)}")
        return []

def apply_discount(products, discount_percentage):
    """Aplica desconto aos produtos"""
    if discount_percentage <= 0:
        return products
        
    for product in products:
        original_price = Decimal(product['price'])
        discount = original_price * (Decimal(discount_percentage) / Decimal('100'))
        product['original_price'] = original_price
        product['discounted_price'] = original_price - discount
        product['discount_applied'] = f"{discount_percentage}%"
        
    return products

# Valor padrão usado quando a configuração não pode ser recuperada (fallback).
DEFAULT_DISCOUNT_FEATURE = {"enabled": False, "discount_percentage": 0}


def get_discount_feature():
    """Retorna a feature flag de desconto, com fallback seguro em caso de erro."""
    try:
        config = get_config() or {}
    except Exception as e:
        logger.error(f"Falha ao obter configuração, usando fallback: {e}")
        return dict(DEFAULT_DISCOUNT_FEATURE)
    return config.get("discount_enabled", DEFAULT_DISCOUNT_FEATURE)


@app.route('/api/products')
def get_product_list():
    discount_feature = get_discount_feature()

    if discount_feature["enabled"]:
        discount_percentage = discount_feature["discount_percentage"]
    else:
       discount_percentage = 0

    # Busca produtos no DynamoDB
    products = get_products()
    
    # Aplica desconto se a feature estiver habilitada
    if discount_feature["enabled"] and discount_percentage > 0:
        products = apply_discount(products, discount_percentage)

    response = {
        'products': products,
        'promotion_active': discount_feature["enabled"],
        'discount_percentage': discount_percentage
    }
    
    return json.dumps(response, default=decimal_default), 200, {'Content-Type': 'application/json'}

@app.route('/api/status')
def status():
    discount_feature = get_discount_feature()

    return jsonify({
        'service': 'backend',
        'status': 'operational',
        'discount_feature': discount_feature,
        'discount_percentage': discount_feature["discount_percentage"] if discount_feature["enabled"] else 0
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)  # nosec B104 - required for container networking
