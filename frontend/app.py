from flask import Flask, render_template, request, redirect, url_for, flash
import requests
import json
import os
import logging

import boto3

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(32).hex())

# Configuração do AWS AppConfig usando o Agent
APP_NAME = os.environ.get('APPCONFIG_APP_ID')
ENV_NAME = os.environ.get('APPCONFIG_ENV_ID')
CONFIG_PROFILE_NAME = os.environ.get('APPCONFIG_CONFIG_ID')
BACKEND_URL = os.environ.get('BACKEND_URL')

# Usados pelo portal /admin para escrever a flag diretamente no AppConfig
AWS_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-west-2')
# Estratégia de deployment instantânea (0 bake). Se ausente, cai para AllAtOnce.
DEPLOY_STRATEGY_ID = os.environ.get('APPCONFIG_DEPLOY_STRATEGY_ID', 'AppConfig.AllAtOnce')

def get_feature_flag():
    """Consulta o status da feature flag e o valor do desconto usando AppConfig Agent"""
    logger.info("Iniciando busca da feature flag de desconto no frontend via AppConfig Agent")
    
    try:
        # Fazer requisição para o AppConfig Agent localmente
        agent_url = f"http://localhost:2772/applications/{APP_NAME}/environments/{ENV_NAME}/configurations/{CONFIG_PROFILE_NAME}"
        logger.info(f"Consultando AppConfig Agent em: {agent_url}")
        
        # Recuperar a configuração completa
        response = requests.get(agent_url, timeout=5)
        
        if response.status_code == 200:
            config_data = json.loads(response.content)
            logger.info(f"Conteúdo da configuração recebido: {json.dumps(config_data)}")
            
            # Tenta processar diferentes formatos de configuração possíveis
            if 'discount_enabled' in config_data:
                # Formato original esperado
                feature = config_data['discount_enabled']
                enabled = feature.get('enabled', False)
                discount = feature.get('discount_percentage', 0)
            elif 'flags' in config_data and 'discount_enabled' in config_data.get('flags', {}):
                # Formato de feature flag do AppConfig
                feature = config_data['flags']['discount_enabled']
                enabled = feature.get('enabled', False)
                discount = feature.get('attributes', {}).get('discount_percentage', 0)
            else:
                # Não foi possível identificar o formato, usando valores padrão
                logger.warning("Formato de configuração não reconhecido")
                enabled = False
                discount = 0
            
            result = {
                'enabled': enabled,
                'discount_percentage': discount
            }
            
            logger.info(f"Feature flag processada: enabled={enabled}, discount_percentage={discount}")
            return result
        else:
            logger.error(f"Erro ao consultar AppConfig Agent. Status: {response.status_code}, Resposta: {response.text}")
            return {'enabled': False, 'discount_percentage': 0}
    except Exception as e:
        logger.error(f"Erro ao buscar configuração via AppConfig Agent: {str(e)}")
        return {'enabled': False, 'discount_percentage': 0}

@app.route('/')
def index():
    logger.info("Requisição para página inicial recebida")
    
    # Consulta a feature flag (para saber se a promoção está ativa)
    feature = get_feature_flag()
    promotion_active = feature['enabled']
    discount_percentage = feature['discount_percentage'] if promotion_active else 0
    
    logger.info(f"Status da feature flag no frontend: enabled={promotion_active}, discount={discount_percentage}%")
    
    try:
        # Consulta o status do backend
        logger.info(f"Consultando status do backend em {BACKEND_URL}/api/status")
        status_response = requests.get(f"{BACKEND_URL}/api/status", timeout=5)
        backend_data = status_response.json()
        logger.info(f"Status do backend: {json.dumps(backend_data)}")
        
        # Consulta a lista de produtos
        logger.info(f"Consultando produtos do backend em {BACKEND_URL}/api/products")
        products_response = requests.get(f"{BACKEND_URL}/api/products", timeout=5)
        products_data = products_response.json()
        products = products_data.get('products', [])
        
        # Pega a informação de promoção e desconto do backend
        # (confie na informação do backend em vez da configuração local)
        promotion_active = products_data.get('promotion_active', False)
        discount_percentage = products_data.get('discount_percentage', 0)
        
        logger.info(f"Produtos recebidos do backend: {len(products)}")
        logger.info(f"Promoção ativa (segundo backend): {promotion_active}")
        logger.info(f"Porcentagem de desconto (segundo backend): {discount_percentage}%")
        
    except Exception as e:
        logger.error(f"Erro ao comunicar com o backend: {str(e)}")
        backend_data = {'error': str(e), 'status': 'unavailable'}
        products = []
    
    logger.info("Renderizando template index.html")
    return render_template('index.html', 
                          promotion_active=promotion_active,
                          discount_percentage=discount_percentage,
                          products=products,
                          backend_data=backend_data)

@app.route('/debug')
def debug():
    """
    ⚠️ DEMONSTRATION ONLY — This route has no authentication and exposes
    internal configuration details (AppConfig IDs, backend URL, raw agent
    payload). It exists so readers can inspect the sidecar integration
    while following the blog post.

    In a non-demonstration environment, remove this endpoint entirely or
    restrict access to an internal network / authenticated users.
    """
    logger.info("Requisição para página de debug recebida")
    
    debug_info = {
        'app_config': {
            'APP_NAME': APP_NAME,
            'ENV_NAME': ENV_NAME,
            'CONFIG_PROFILE_NAME': CONFIG_PROFILE_NAME,
            'BACKEND_URL': BACKEND_URL
        },
        'feature_flag': get_feature_flag()
    }
    
    try:
        # Consultar o AppConfig Agent diretamente para debug
        agent_url = f"http://localhost:2772/applications/{APP_NAME}/environments/{ENV_NAME}/configurations/{CONFIG_PROFILE_NAME}"
        agent_response = requests.get(agent_url, timeout=5)
        debug_info['appconfig_agent_raw'] = json.loads(agent_response.content) if agent_response.status_code == 200 else {"error": f"Status {agent_response.status_code}"}
        
        # Tenta obter status do backend
        status_response = requests.get(f"{BACKEND_URL}/api/status", timeout=5)
        debug_info['backend_status'] = status_response.json()
        
        # Tenta obter produtos do backend
        products_response = requests.get(f"{BACKEND_URL}/api/products", timeout=5)
        debug_info['backend_products'] = products_response.json()
    except Exception as e:
        debug_info['backend_status'] = {'error': str(e)}
    
    logger.info(f"Informações de debug: {json.dumps(debug_info, default=str)}")
    return render_template('debug.html', debug_info=debug_info)

# ---------------------------------------------------------------------------
# Portal de administração da feature flag (/admin)
# Escreve diretamente no AppConfig (control plane) via boto3: cria uma nova
# hosted configuration version e dispara um deployment. O AppConfig Agent
# sidecar detecta a mudança no próximo poll e as apps passam a servir o novo valor.
# ---------------------------------------------------------------------------

def _appconfig_client():
    return boto3.client('appconfig', region_name=AWS_REGION)


def _build_flag_content(enabled, discount_percentage):
    """Monta o JSON de feature flags no formato esperado pelo AppConfig."""
    return {
        "flags": {
            "discount_enabled": {
                "name": "discount_enabled",
                "attributes": {
                    "discount_percentage": {
                        "constraints": {"type": "number", "minimum": 0, "maximum": 100}
                    }
                }
            }
        },
        "values": {
            "discount_enabled": {
                "enabled": bool(enabled),
                "discount_percentage": int(discount_percentage)
            }
        },
        "version": "1"
    }


def set_flag(enabled, discount_percentage):
    """Cria uma nova versão da configuração e dispara o deployment."""
    client = _appconfig_client()
    content = json.dumps(_build_flag_content(enabled, discount_percentage)).encode('utf-8')

    version = client.create_hosted_configuration_version(
        ApplicationId=APP_NAME,
        ConfigurationProfileId=CONFIG_PROFILE_NAME,
        Content=content,
        ContentType='application/json'
    )
    version_number = version['VersionNumber']
    logger.info(f"Nova hosted version criada: {version_number}")

    client.start_deployment(
        ApplicationId=APP_NAME,
        EnvironmentId=ENV_NAME,
        DeploymentStrategyId=DEPLOY_STRATEGY_ID,
        ConfigurationProfileId=CONFIG_PROFILE_NAME,
        ConfigurationVersion=str(version_number),
        Description=f"Toggle via portal /admin: enabled={enabled}, discount={discount_percentage}%"
    )
    logger.info("Deployment iniciado")
    return version_number


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    """
    ⚠️ DEMONSTRATION ONLY — This admin portal has no authentication.
    It is intentionally open so readers can toggle the feature flag and
    observe the real-time propagation through the AppConfig Agent sidecar
    without additional setup.

    In a production environment, protect this endpoint with an
    authentication/authorization layer (e.g., Amazon Cognito + ALB OIDC,
    API Gateway authorizer, or application-level middleware).
    """
    if request.method == 'POST':
        enabled = request.form.get('enabled') == 'on'
        try:
            discount = int(request.form.get('discount_percentage', 0))
        except ValueError:
            discount = 0
        discount = max(0, min(100, discount))
        try:
            version = set_flag(enabled, discount)
            state = 'ENABLED' if enabled else 'DISABLED'
            flash(f"Promotion {state} ({discount}%). New version {version} published — "
                  f"the change propagates within seconds.", 'success')
        except Exception as e:
            logger.error(f"Error updating the flag: {e}")
            flash(f"Error updating the flag: {e}", 'error')
        return redirect(url_for('admin'))

    # GET: mostra o estado atual (lido via o AppConfig Agent local)
    feature = get_feature_flag()
    return render_template('admin.html', feature=feature)


if __name__ == '__main__':
    logger.info("Iniciando aplicação frontend")
    app.run(host='0.0.0.0', port=80)  # nosec B104 - required for container networking