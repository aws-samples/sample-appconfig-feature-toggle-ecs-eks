from flask import Flask, render_template
import requests
import json
import os
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

app = Flask(__name__)

# Configuração do AWS AppConfig usando o Agent
APP_NAME = os.environ.get('APPCONFIG_APP_ID')
ENV_NAME = os.environ.get('APPCONFIG_ENV_ID')
CONFIG_PROFILE_NAME = os.environ.get('APPCONFIG_CONFIG_ID')
BACKEND_URL = os.environ.get('BACKEND_URL')

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
    """Página de debug para visualizar configurações e status"""
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

if __name__ == '__main__':
    logger.info("Iniciando aplicação frontend")
    app.run(host='0.0.0.0', port=80)