import telebot
import requests
import json
import time
import threading
import schedule
from telebot import types
from datetime import datetime
from flask import Flask
import mercadopago
import uuid
import os

TOKEN = '8810418278:AAGAy3zXey3OYn96rHJjzx45yHF6HQ8UqEI'
ADMIN_ID = 8273924319
SMS_ACTIVATE_API = '6ecc01ce6fAfdA8fc2b47562b9249955' # Chave HeroSMS atualizada
 # Removido 5sim conforme solicitado
MERCADOPAGO_ACCESS_TOKEN = 'APP_USR-7763590755819948-051518-e32585685e8916fbbdb1defd883729be-251464317'
sdk = mercadopago.SDK(MERCADOPAGO_ACCESS_TOKEN)

bot = telebot.TeleBot(TOKEN)
lista_usuarios = set() # Para o sistema de broadcast

usuarios = {}
saldos = {}
compras = {}
geracoes = {}
bloqueios = {}
recargas_pendentes = {}
ultima_requisicao = {}
pagamentos_pendentes = {}
mensagens_bot = {}
favoritos = {} # {user_id: [service_codes]}
historico_compras = {} # {user_id: total_compras}
operadoras_preferidas = {} # {user_id: operator_name}
afiliados = {} # {user_id: [referrals]}
indicado_por = {} # {user_id: referrer_id}
user_country = {} # {user_id: country_id}

PAISES = {
    '73': 'Brasil 🇧🇷',
    '12': 'EUA 🇺🇸',
    '6': 'Indonésia 🇮🇩',
    '0': 'Rússia 🇷🇺',
    '16': 'Inglaterra 🇬🇧',
    '86': 'Itália 🇮🇹',
    '21': 'França 🇫🇷',
    '22': 'Alemanha 🇩🇪'
}

def apagar_msg_usuario(m):
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except:
        pass

def enviar_e_limpar(chat_id, texto, markup=None, parse_mode=None, photo_path=None):
    user_id = chat_id
    if user_id in mensagens_bot:
        try:
            bot.delete_message(chat_id, mensagens_bot[user_id])
        except:
            pass
    
    try:
        if photo_path:
            with open(photo_path, 'rb') as photo:
                msg = bot.send_photo(chat_id, photo, caption=texto, reply_markup=markup, parse_mode=parse_mode)
        else:
            msg = bot.send_message(chat_id, texto, reply_markup=markup, parse_mode=parse_mode)
    except:
        # Fallback caso a foto falhe
        msg = bot.send_message(chat_id, texto, reply_markup=markup, parse_mode=parse_mode)
        
    mensagens_bot[user_id] = msg.message_id
    return msg
def gerar_pix(valor, user_id):
    payment_data = {
        "transaction_amount": float(valor),
        "description": f"Recarga Saldo Bot - User {user_id}",
        "payment_method_id": "pix",
        "payer": {
            "email": f"user_{user_id}@test.com",
            "first_name": "User",
            "last_name": str(user_id)
        }
    }
    try:
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response.get("response", {})
        
        if payment_response.get("status") == 401:
            return None, "Token bloqueado ou conta não ativada (Unauthorized use of live credentials)."
            
        if "id" in payment and "point_of_interaction" in payment:
            pix_dict = payment["point_of_interaction"]["transaction_data"]
            return {
                "id": payment["id"],
                "qr_code_base64": pix_dict.get("qr_code_base64"),
                "qr_code": pix_dict.get("qr_code")
            }, None
            
        return None, payment.get("message", "Erro desconhecido na API do Mercado Pago.")
    except Exception as e:
        print("Erro MP:", e)
        return None, str(e)

def verificar_pagamento(payment_id):
    try:
        payment_info = sdk.payment().get(payment_id)
        if "response" in payment_info:
            return payment_info["response"].get("status") == "approved"
        return False
    except:
        return False

PRECOS_BASE = {
    'wa': 12.00, 'tg': 10.20, 'ig': 4.50, 'fb': 3.50, 'go': 4.00,
    'tk': 3.00, 'tw': 3.00, 'ub': 2.50, 'nf': 2.50, 'ds': 2.50,
    'ot': 5.00
}

SERVICOS_GRID = [
    ('wa', 'WhatsApp'), ('tg', 'Telegram'),
    ('ig', 'Instagram'), ('fb', 'Facebook'),
    ('go', 'Google/YouTube'), ('tk', 'TikTok'),
    ('tw', 'Twitter/X'), ('ds', 'Discord'),
    ('ub', 'Uber'), ('nf', 'Netflix'),
    ('am', 'Amazon'), ('ab', 'Airbnb'),
    ('ba', 'Badoo'), ('tl', 'Tinder'),
    ('mt', 'Microsoft'), ('st', 'Steam'),
    ('hw', 'Huawei'), ('ym', 'Yahoo'),
    ('ot', 'Outros Apps')
]

SERVICOS = dict(SERVICOS_GRID)
SERVICOS['wa'] = 'Whatsapp'
SERVICOS['tg'] = 'Telegram'
SERVICOS['ot'] = 'Outros apps'

MULTIPLICADOR_LUCRO = 7.0
MODO_MANUTENCAO = False
cache_precos_api = {}

def atualizar_precos_api():
    global cache_precos_api
    # Busca preços para os principais países
    for cid in PAISES.keys():
        url = f"https://hero-sms.com/stubs/handler_api.php?api_key={SMS_ACTIVATE_API}&action=getPrices&country={cid}"
        try:
            res = requests.get(url, timeout=10)
            data = res.json()
            if cid in data:
                if cid not in cache_precos_api: cache_precos_api[cid] = {}
                for service, details in data[cid].items():
                    costs = list(details.values())[0] if isinstance(details, dict) else details
                    if isinstance(costs, dict):
                        cost = costs.get('cost', 0)
                        count = costs.get('count', 0)
                        cache_precos_api[cid][service] = float(cost)
                        cache_precos_api[cid][f"{service}_count"] = int(count)
        except: pass

threading.Thread(target=atualizar_precos_api).start()

demanda = {k: 0 for k in PRECOS_BASE.keys()}

def calcular_preco(service_code, country_id='73'):
    precos_pais = cache_precos_api.get(str(country_id), {})
    custo_base_api = precos_pais.get(service_code, PRECOS_BASE.get(service_code, 1.0))
    preco_venda = custo_base_api * MULTIPLICADOR_LUCRO
    pontos = demanda.get(service_code, 0)
    preco_final = preco_venda + (pontos * 0.50)
    
    if service_code == 'wa' and country_id == '73':
        # WhatsApp Brasil varia entre R$ 11,00 e R$ 21,00
        return max(min(preco_final, 21.0), 11.0)
    
    # Geral varia entre R$ 5,00 e R$ 30,00
    return max(min(preco_final, 30.0), 5.0)

def api_get_number(service_code, country_id='73'):
    # Tenta HeroSMS (Sucessor oficial do SMS-Activate)
    url = f"https://hero-sms.com/stubs/handler_api.php?api_key={SMS_ACTIVATE_API}&action=getNumber&service={service_code}&country={country_id}"
    try:
        res = requests.get(url, timeout=10)
        text = res.text
        if text.startswith("ACCESS_NUMBER"):
            parts = text.split(":")
            return parts[1], parts[2], "herosms"
        if text == "NO_NUMBERS":
            return None, f"Sem estoque no HeroSMS ({PAISES.get(str(country_id), country_id)})", None
    except Exception:
        pass 

    return None, "Erro de conexão ou falta de estoque no HeroSMS.", None

def api_get_sms(id_order, provider="herosms"):
    url = f"https://hero-sms.com/stubs/handler_api.php?api_key={SMS_ACTIVATE_API}&action=getStatus&id={id_order}"
    try:
        res = requests.get(url, timeout=10)
        text = res.text
        if text.startswith("STATUS_OK"):
            parts = text.split(":")
            return parts[1]
        return None
    except Exception:
        return None

def api_cancel_number(id_order, provider="herosms"):
    url = f"https://hero-sms.com/stubs/handler_api.php?api_key={SMS_ACTIVATE_API}&action=setStatus&status=8&id={id_order}"
    try:
        requests.get(url, timeout=10)
    except:
        pass

def background_check_sms(chat_id, user_id, id_order, message_id, provider):
    tempo_inicio = time.time()
    while time.time() - tempo_inicio < 900: # 15 minutos de monitoramento
        # Verifica se o pedido ainda é o mesmo (não foi cancelado ou substituído)
        if user_id not in compras or not compras[user_id] or compras[user_id]['id_order'] != id_order:
            return # Encerra a thread se o pedido sumiu ou mudou

        sms_code = api_get_sms(id_order, provider)
        if sms_code:
            try:
                compra = compras[user_id]
                msg = f"✨ **SMS RECEBIDO COM SUCESSO!** ✨\n"
                msg += f"━━━━━━━━━━━━━━━━━━━━\n"
                msg += f"📱 **Número:** `{compra['number']}`\n"
                msg += f"💬 **Código:** `{sms_code}`\n"
                msg += f"━━━━━━━━━━━━━━━━━━━━\n"
                msg += f"✅ Detectado automaticamente. Copie e use agora!"
                
                bot.edit_message_text(msg, chat_id, message_id, parse_mode="Markdown")
                # Envia uma nova mensagem para garantir que o usuário receba a notificação sonora
                bot.send_message(chat_id, f"🎉 **CHEGOU!**\nSeu código é: `{sms_code}`", parse_mode="Markdown")
            except:
                pass
            
            compras[user_id] = None # Finaliza o pedido no sistema
            return
            
        time.sleep(8) # Intervalo entre checagens

@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text == 'Start 🔄')
def start(m):
    user_id = m.from_user.id
    
    # Check Ban
    if bloqueios.get(user_id):
        bot.send_message(m.chat.id, "❌ **ACESSO NEGADO**\n\nSua conta foi suspensa por violar nossos termos de uso. Entre em contato com o suporte para mais informações.", parse_mode="Markdown")
        return

    # Check Maintenance
    if MODO_MANUTENCAO and user_id != ADMIN_ID:
        bot.send_message(m.chat.id, "🛠 **MODO MANUTENÇÃO**\n\nEstamos realizando melhorias no sistema. Voltaremos em breve! Acompanhe as novidades no canal oficial.", parse_mode="Markdown")
        return

    apagar_msg_usuario(m)
    user_id = m.from_user.id
    lista_usuarios.add(user_id) # Adiciona à lista de broadcast
    if user_id not in saldos:
        saldos[user_id] = 0.0
    
    # Lógica de Afiliados (se for novo usuário e tiver vindo de link)
    if m.text.startswith('/start ') and len(m.text.split()) > 1:
        ref_id = m.text.split()[1]
        try:
            ref_id = int(ref_id)
            if ref_id != user_id and user_id not in indicado_por:
                indicado_por[user_id] = ref_id
                if ref_id not in afiliados: afiliados[ref_id] = []
                afiliados[ref_id].append(user_id)
        except: pass
        
    menu_principal(m)

@bot.message_handler(commands=['recarregar'])
def cmd_recarregar(m):
    menu_adicionar_saldo(m)

@bot.message_handler(commands=['servicos'])
def cmd_servicos(m):
    comprar_numero_menu(m)

@bot.message_handler(commands=['afiliados'])
def cmd_afiliados(m):
    # Cria um objeto fake de call para reaproveitar a função
    class FakeCall:
        def __init__(self, m):
            self.message = m
            self.from_user = m.from_user
    handle_afiliados(FakeCall(m))

@bot.message_handler(commands=['pin'])
def cmd_pin(m):
    apagar_msg_usuario(m)
    user_id = m.from_user.id
    msg = f"🔑 **SEU PIN DE CLIENTE**\n\nSeu identificador único é: `{user_id}`\n\nUse este PIN para suporte ou identificação em depósitos manuais."
    enviar_e_limpar(m.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['contas'])
def cmd_contas(m):
    apagar_msg_usuario(m)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="main_menu_back"))
    msg = "🛒 **COMPRA DE CONTAS PRONTAS**\n\nEsta seção está em manutenção. Em breve você poderá comprar contas de WhatsApp, Telegram e Instagram já maturadas!"
    enviar_e_limpar(m.chat.id, msg, markup=markup, parse_mode="Markdown")

def menu_principal(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('📱 Comprar Número')
    markup.row('💳 Recarregar', '⚙️ Configurações')
    
    name = m.from_user.first_name
    username = m.from_user.username
    display_name = f"@{username}" if username else name.upper()
    user_id = m.from_user.id
    saldo = saldos.get(user_id, 0.0)
    
    msg = f"⭐ **BEM-VINDO, {display_name}!** ⭐\n\n"
    msg += f"👤 **Cliente:** [`ID-{user_id}`](tg://user?id={user_id})\n"
    msg += f"💰 **Saldo:** [R$ {saldo:.2f}](tg://user?id={user_id})\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🚀 **SELECIONE UMA OPÇÃO ABAIXO:**\n"
    msg += f"Navegue pelos botões para gerar números.\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🆘 **Suporte:** @CORVO291\n"
    msg += f"🛡 **Seguro & Criptografado**"
    
    enviar_e_limpar(m.chat.id, msg, markup=markup, photo_path="banner_start.png", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == '💳 Recarregar' or m.text == '• Recarregar')
def btn_recarregar(m):
    menu_adicionar_saldo(m)

@bot.message_handler(func=lambda m: m.text == '📱 Comprar Número' or m.text == '• Gerar Número')
def btn_gerar(m):
    comprar_numero_menu(m)

@bot.message_handler(func=lambda m: m.text == '⚙️ Configurações')
def btn_config(m):
    apagar_msg_usuario(m)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("★ Selecionar Favoritos", callback_data="menu_fav"))
    markup.row(types.InlineKeyboardButton("📊 Serviços mais comprados", callback_data="menu_stats"))
    markup.row(types.InlineKeyboardButton("📞 Selecionar operadora", callback_data="menu_carrier"))
    markup.row(types.InlineKeyboardButton("🔔 Setup de alertas", callback_data="menu_alerts"))
    markup.row(types.InlineKeyboardButton("💸 Transferir saldo", callback_data="menu_transfer"))
    markup.row(types.InlineKeyboardButton("👤 Perfil", callback_data="menu_profile"))
    markup.row(types.InlineKeyboardButton("❌ Deletar conta e dados", callback_data="menu_delete"))
    markup.row(types.InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="main_menu_back"))
    
    enviar_e_limpar(m.chat.id, "⚙️ **CONFIGURAÇÕES DO SISTEMA**\n\nPersonalize sua experiência e gerencie seus dados abaixo:", markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == '💳 Adicionar Saldo')
def menu_adicionar_saldo(m):
    apagar_msg_usuario(m)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="main_menu_back"))
    msg = enviar_e_limpar(m.chat.id, "💰 **ADICIONAR SALDO**\n\nDigite o valor que deseja recarregar (Mínimo: R$ 15):\nExemplo: `20.00`", markup=markup, parse_mode="Markdown")
    bot.register_next_step_handler(msg, processar_valor_recarga)

@bot.callback_query_handler(func=lambda call: call.data == 'main_menu_back')
def handle_main_menu_back(call):
    menu_principal(call.message)

def processar_valor_recarga(m):
    apagar_msg_usuario(m)
    if m.text in ['📱 Comprar Número', '📥 Receber SMS', 'Seu Saldo', 'Start 🔄', '💳 Adicionar Saldo']:
        menu_principal(m)
        return
    try:
        valor = float(m.text.replace(',', '.'))
        if valor < 15.0:
            enviar_e_limpar(m.chat.id, "❌ O valor mínimo é R$ 15,00.")
            return
        
        # Bônus para o Afiliado (10%)
        user_id = m.from_user.id
        if user_id in indicado_por:
            ref_id = indicado_por[user_id]
            bonus = valor * 0.10
            saldos[ref_id] = saldos.get(ref_id, 0.0) + bonus
            try: bot.send_message(ref_id, f"🎊 **Bônus de Afiliado!**\n\nSeu indicado `{user_id}` recarregou R$ {valor:.2f} e você ganhou R$ {bonus:.2f} de comissão!", parse_mode="Markdown")
            except: pass

        enviar_e_limpar(m.chat.id, "⏳ Gerando PIX Copia e Cola, aguarde...")
        pix_data, erro_mp = gerar_pix(valor, m.from_user.id)
        if pix_data:
            pagamentos_pendentes[m.from_user.id] = {
                "id": pix_data["id"],
                "valor": valor
            }
            msg = f"✅ **PIX Gerado com Sucesso!**\n\n"
            msg += f"Valor: R$ {valor:.2f}\n\n"
            msg += f"Copie o código abaixo e pague no app do seu banco:\n\n"
            msg += f"`{pix_data['qr_code']}`\n\n"
            msg += "Após pagar, clique no botão abaixo para receber o saldo."
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Já Paguei / Verificar", callback_data="check_pix"))
            enviar_e_limpar(m.chat.id, msg, parse_mode="Markdown", markup=markup)
        else:
            enviar_e_limpar(m.chat.id, f"❌ Erro ao gerar PIX: {erro_mp}\n\nVerifique se ativou as credenciais de produção no site do MP.")
    except ValueError:
        enviar_e_limpar(m.chat.id, "❌ Valor inválido. Digite apenas números, como 5 ou 10.50.")

@bot.callback_query_handler(func=lambda call: call.data == 'check_pix')
def handle_check_pix(call):
    user_id = call.from_user.id
    if user_id not in pagamentos_pendentes or not pagamentos_pendentes[user_id]:
        bot.answer_callback_query(call.id, "Nenhum pagamento pendente encontrado.", show_alert=True)
        return
    payment = pagamentos_pendentes[user_id]
    
    enviar_e_limpar(call.message.chat.id, "⏳ Verificando pagamento no Mercado Pago...")
    
    if verificar_pagamento(payment['id']):
        if user_id not in saldos:
            saldos[user_id] = 0
        saldos[user_id] += payment['valor']
        pagamentos_pendentes[user_id] = None
        enviar_e_limpar(call.message.chat.id, f"🎉 **Pagamento Aprovado!**\n\nR$ {payment['valor']:.2f} adicionados ao seu saldo.\n💰 Seu novo saldo é R$ {saldos[user_id]:.2f}", parse_mode="Markdown")
    else:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔄 Verificar Novamente", callback_data="check_pix"))
        enviar_e_limpar(call.message.chat.id, f"⚠️ Pagamento não consta como aprovado.\n\nSe você já pagou, aguarde 1 minuto e tente de novo.", markup=markup)

@bot.message_handler(func=lambda m: m.text == 'Seu Saldo')
def ver_saldo(m):
    apagar_msg_usuario(m)
    user_id = m.from_user.id
    saldo = saldos.get(user_id, 0.0)
    enviar_e_limpar(m.chat.id, f"💰 Seu saldo é R${saldo:.2f}")

@bot.message_handler(func=lambda m: m.text == '📱 Comprar Número' or m.text == '• Gerar Número')
def comprar_numero_menu(m, filter_fav=False):
    apagar_msg_usuario(m)
    user_id = m.from_user.id if hasattr(m, 'from_user') else m.chat.id
    cid = str(user_country.get(user_id, '73'))
    markup = types.InlineKeyboardMarkup()
    
    # Categorias e organização
    msg = f"💎 **MENU DE COMPRAS** 💎\n"
    msg += f"📍 **País:** {PAISES.get(cid, 'Brasil')}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if filter_fav:
        msg = f"⭐ **MEUS FAVORITOS** ⭐\n"
        msg += f"📍 **País:** {PAISES.get(cid, 'Brasil')}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        servicos = [s for s in SERVICOS_GRID if s[0] in favoritos.get(user_id, [])]
    else:
        servicos = SERVICOS_GRID

    if not servicos and filter_fav:
        msg += "Você ainda não tem favoritos. Adicione em Configurações!"

    for i in range(0, len(servicos), 2):
        s1 = servicos[i]
        p1 = calcular_preco(s1[0], cid)
        btn1 = types.InlineKeyboardButton(f"{s1[1]} | R${p1:.2f}", callback_data=f"buy_{s1[0]}")
        if i+1 < len(servicos):
            s2 = servicos[i+1]
            p2 = calcular_preco(s2[0], cid)
            btn2 = types.InlineKeyboardButton(f"{s2[1]} | R${p2:.2f}", callback_data=f"buy_{s2[0]}")
            markup.row(btn1, btn2)
        else: markup.row(btn1)

    # Rodapé do menu
    markup.row(types.InlineKeyboardButton("🌍 Trocar País", callback_data="btn_change_country"))
    markup.row(types.InlineKeyboardButton("🔄 Atualizar", callback_data="buy_menu_back"))
    txt_fav = "🏠 Ver Todos" if filter_fav else "⭐ Ver Favoritos"
    markup.row(types.InlineKeyboardButton(txt_fav, callback_data="toggle_filter_fav"), types.InlineKeyboardButton("🔗 Afiliados", callback_data="menu_afiliados"))
    markup.row(types.InlineKeyboardButton("💳 Adicionar Saldo", callback_data="menu_add_saldo"))
    markup.row(types.InlineKeyboardButton("⬅️ Voltar", callback_data="main_menu_back"))
    
    enviar_e_limpar(m.chat.id if hasattr(m, 'chat') else m.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'btn_change_country')
def handle_change_country_menu(call):
    markup = types.InlineKeyboardMarkup()
    for cid, name in PAISES.items():
        markup.add(types.InlineKeyboardButton(name, callback_data=f"set_country_{cid}"))
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="buy_menu_back"))
    enviar_e_limpar(call.message.chat.id, "🌍 **SELECIONAR PAÍS**\n\nEscolha o país de origem do número:", markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_country_'))
def set_user_country(call):
    cid = call.data.split('_')[2]
    user_id = call.from_user.id
    user_country[user_id] = cid
    bot.answer_callback_query(call.id, f"📍 País alterado para {PAISES.get(cid)}", show_alert=False)
    comprar_numero_menu(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'toggle_filter_fav')
def handle_toggle_filter(call):
    is_filtered = "FILTRO" in call.message.text
    comprar_numero_menu(call, filter_fav=not is_filtered)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_afiliados')
def handle_afiliados(call):
    user_id = call.from_user.id
    link = f"https://t.me/{(bot.get_me().username)}?start={user_id}"
    total_ref = len(afiliados.get(user_id, []))
    
    msg = "💰 **SISTEMA DE AFILIADOS**\n\n"
    msg += "Convide pessoas para o bot e ganhe **10% de bônus** sobre cada recarga que elas fizerem!\n\n"
    msg += f"🔗 **Seu link único:**\n`{link}`\n\n"
    msg += f"👥 Indicados: {total_ref}\n"
    msg += "💸 O bônus cai direto no seu saldo."
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="buy_menu_back"))
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'buy_menu_back')
def buy_back(call):
    comprar_numero_menu(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_lista')
def next_list(call):
    bot.answer_callback_query(call.id, "📚 Você já está na lista principal. Novos serviços serão adicionados automaticamente!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def handle_buy(call):
    user_id = call.from_user.id
    service_code = call.data.split('_')[1]
    preco = calcular_preco(service_code)
    saldo_atual = saldos.get(user_id, 0.0)
    
    if saldo_atual < preco:
        bot.answer_callback_query(call.id, f"Saldo insuficiente! Você precisa de R${preco:.2f}.", show_alert=True)
        return
        
    service_name = SERVICOS.get(service_code, "Serviço")
    
    enviar_e_limpar(call.message.chat.id, f"⏳ Solicitando número de {service_name} (Brasil)...")
    
    id_order, number, provider = api_get_number(service_code, user_country.get(user_id, '73'))
    
    if id_order:
        saldos[user_id] -= preco
        compras[user_id] = {'id_order': id_order, 'number': number, 'service': service_name, 'service_code': service_code, 'preco_pago': preco, 'provider': provider}
        
        # Incrementa histórico
        historico_compras[user_id] = historico_compras.get(user_id, 0) + 1
        
        # Sobe a demanda do serviço (limite +10)
        if demanda.get(service_code, 0) < 10:
            demanda[service_code] = demanda.get(service_code, 0) + 1
        
        msg = f"✅ **NÚMERO GERADO!**\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"📱 **Serviço:** {service_name}\n"
        msg += f"📞 **Número:** `{number}`\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += "⏳ **Aguardando SMS automaticamente...**\n"
        msg += "O código aparecerá aqui assim que chegar."
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔄 Verificar Agora", callback_data="check_sms_now"))
        markup.add(types.InlineKeyboardButton("❌ Cancelar / Estornar", callback_data="cancel_number"))
        
        sent_msg = enviar_e_limpar(call.message.chat.id, msg, parse_mode="Markdown", markup=markup)
        
        # Inicia o monitoramento automático em uma thread separada
        threading.Thread(target=background_check_sms, args=(call.message.chat.id, user_id, id_order, sent_msg.message_id, provider), daemon=True).start()
    else:
        enviar_e_limpar(call.message.chat.id, f"❌ **ERRO DE ESTOQUE**\n\nNão foi possível obter um número de {service_name} no momento. Tente novamente em instantes.")

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_number')
def handle_cancel(call):
    user_id = call.from_user.id
    if user_id in compras and compras[user_id]:
        id_order = compras[user_id]['id_order']
        provider = compras[user_id].get('provider', 'sms_activate')
        service_code = compras[user_id].get('service_code', 'wa')
        preco = compras[user_id].get('preco_pago', calcular_preco(service_code))
        
        api_cancel_number(id_order, provider)
        saldos[user_id] += preco
        compras[user_id] = None
        
        # Reduz a demanda porque a compra não foi concluída com sucesso
        if demanda.get(service_code, 0) > -4:
            demanda[service_code] = demanda.get(service_code, 0) - 1
            
        enviar_e_limpar(call.message.chat.id, f"✅ Número cancelado e R${preco:.2f} reembolsados ao seu saldo.")
    else:
        bot.answer_callback_query(call.id, "Nenhum número ativo para cancelar.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_profile')
def handle_profile(call):
    user_id = call.from_user.id
    saldo = saldos.get(user_id, 0.0)
    total = historico_compras.get(user_id, 0)
    msg = f"👤 **SEU PERFIL PROFISSIONAL**\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🆔 **Seu ID:** `{user_id}`\n"
    msg += f"💰 **Saldo:** R$ {saldo:.2f}\n"
    msg += f"📱 **Total de números:** {total}\n"
    msg += f"📞 **Operadora:** {operadoras_preferidas.get(user_id, 'Padrão')}\n"
    msg += f"📍 **País:** {PAISES.get(str(user_country.get(user_id, '73')), 'Brasil')}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += "🚀 **Sistema de Números Virtuais de Alta Qualidade**"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'btn_config_voltar')
def config_voltar(call):
    # Simula o clique no botão de configurações para voltar ao menu
    btn_config(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_delete')
def handle_delete(call):
    user_id = call.from_user.id
    saldos[user_id] = 0.0
    historico_compras[user_id] = 0
    favoritos[user_id] = []
    bot.answer_callback_query(call.id, "❌ Todos os seus dados locais foram deletados e saldo zerado.", show_alert=True)
    menu_principal(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_stats')
def handle_stats(call):
    # Ordena os serviços por demanda (mais comprados)
    mais_comprados = sorted(demanda.items(), key=lambda x: x[1], reverse=True)[:8]
    msg = "📊 **SERVIÇOS EM ALTA (HOT)**\n\n"
    msg += "Estes são os serviços com mais estoque e vendas no momento:\n\n"
    for code, pts in mais_comprados:
        nome = SERVICOS.get(code, code)
        icon = "🔥" if pts > 5 else "✅"
        msg += f"{icon} **{nome}**: {pts + 15} ativações recentes\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'menu_transfer')
def handle_transfer(call):
    msg = enviar_e_limpar(call.message.chat.id, "💸 **TRANSFERÊNCIA DE SALDO**\n\nDigite o **ID** do usuário que vai receber o saldo:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, processar_id_transferencia)

def processar_id_transferencia(m):
    apagar_msg_usuario(m)
    try:
        dest_id = int(m.text)
        msg = enviar_e_limpar(m.chat.id, f"✅ ID `{dest_id}` selecionado.\n\nAgora digite o **VALOR** que deseja transferir:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda msg: processar_valor_transferencia(msg, dest_id))
    except:
        enviar_e_limpar(m.chat.id, "❌ ID inválido. Tente novamente clicando em Transferir.")

def processar_valor_transferencia(m, dest_id):
    apagar_msg_usuario(m)
    user_id = m.from_user.id
    try:
        valor = float(m.text.replace(',', '.'))
        if valor <= 0 or saldos.get(user_id, 0.0) < valor:
            enviar_e_limpar(m.chat.id, "❌ Saldo insuficiente ou valor inválido.")
            return
        
        # Realiza a transferência
        saldos[user_id] -= valor
        saldos[dest_id] = saldos.get(dest_id, 0.0) + valor
        
        enviar_e_limpar(m.chat.id, f"✅ **Transferência Concluída!**\n\nEnviado: R$ {valor:.2f}\nPara: `{dest_id}`", parse_mode="Markdown")
        # Notifica o recebedor se possível
        try: bot.send_message(dest_id, f"💰 Você recebeu uma transferência de R$ {valor:.2f} do usuário `{user_id}`!")
        except: pass
    except:
        enviar_e_limpar(m.chat.id, "❌ Erro no valor. Use números como 5.50")

@bot.callback_query_handler(func=lambda call: call.data == 'menu_carrier')
def handle_carrier(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("Qualquer uma", callback_data="set_op_any"))
    markup.row(types.InlineKeyboardButton("Vivo", callback_data="set_op_vivo"), types.InlineKeyboardButton("Tim", callback_data="set_op_tim"))
    markup.row(types.InlineKeyboardButton("Claro", callback_data="set_op_claro"), types.InlineKeyboardButton("Oi", callback_data="set_op_oi"))
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, "📞 **SELECIONAR OPERADORA**\n\nEscolha sua preferência para os números do Brasil:", markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_op_'))
def set_operator(call):
    op = call.data.split('_')[2]
    user_id = call.from_user.id
    operadoras_preferidas[user_id] = op.capitalize()
    bot.answer_callback_query(call.id, f"✅ Operadora {op.capitalize()} selecionada!", show_alert=True)
    handle_carrier(call)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_fav')
def handle_fav_menu(call):
    markup = types.InlineKeyboardMarkup()
    # Lista os principais serviços para favoritar
    for code, name in SERVICOS_GRID[:10]: # Mostra os 10 primeiros para escolha
        status = "⭐" if code in favoritos.get(call.from_user.id, []) else "☆"
        markup.add(types.InlineKeyboardButton(f"{status} {name}", callback_data=f"toggle_fav_{code}"))
    
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, "★ **GERENCIAR FAVORITOS**\n\nOs serviços selecionados aparecerão no topo do menu de compras:", markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_fav_'))
def toggle_fav(call):
    code = call.data.split('_')[2]
    user_id = call.from_user.id
    if user_id not in favoritos: favoritos[user_id] = []
    if code in favoritos[user_id]:
        favoritos[user_id].remove(code)
    else:
        favoritos[user_id].append(code)
    handle_fav_menu(call)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_alerts')
def handle_alerts(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔔 Ativar para TODOS", callback_data="set_alert_all"))
    markup.add(types.InlineKeyboardButton("🔕 Desativar alertas", callback_data="set_alert_none"))
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    
    msg = "🔔 **CENTRAL DE ALERTAS**\n\n"
    msg += "Deseja ser notificado quando houver novos números em estoque?"
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'set_alert_all')
def set_alert_all(call):
    bot.answer_callback_query(call.id, "✅ Alertas ativados para todos os serviços!", show_alert=True)
    handle_alerts(call)

@bot.callback_query_handler(func=lambda call: call.data == 'set_alert_none')
def set_alert_none(call):
    bot.answer_callback_query(call.id, "🔕 Alertas desativados.", show_alert=True)
    handle_alerts(call)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_add_saldo')
def handle_menu_add_saldo(call):
    menu_adicionar_saldo(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'check_sms_now')
def handle_check_sms_now(call):
    receber_sms(call.message)

@bot.message_handler(func=lambda m: m.text == '📥 Receber SMS')
def receber_sms(m):
    apagar_msg_usuario(m)
    user_id = m.from_user.id if hasattr(m, 'from_user') else m.chat.id
    if user_id not in compras or not compras[user_id]:
        enviar_e_limpar(m.chat.id if hasattr(m, 'chat') else m.chat.id, "❌ Você não tem nenhum número aguardando SMS no momento. Compre um número primeiro.")
        return
        
    compra = compras[user_id]
    id_order = compra['id_order']
    provider = compra.get('provider', 'sms_activate')
    
    enviar_e_limpar(m.chat.id if hasattr(m, 'chat') else m.chat.id, f"⏳ Verificando SMS na API ({provider})... Aguarde.")
    
    sms_code = api_get_sms(id_order, provider)
    
    if sms_code:
        msg = f"✅ **O SMS CHEGOU!**\n\n"
        msg += f"Serviço: {compra['service']}\n"
        msg += f"Código: `{sms_code}`\n\n"
        msg += "Use o código no app correspondente. O número foi finalizado com sucesso."
        enviar_e_limpar(m.chat.id if hasattr(m, 'chat') else m.chat.id, msg, parse_mode="Markdown")
        compras[user_id] = None # Finaliza o pedido
    else:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔄 Tentar Novamente", callback_data="check_sms_now"))
        markup.add(types.InlineKeyboardButton("❌ Cancelar Número", callback_data="cancel_number"))
        enviar_e_limpar(m.chat.id if hasattr(m, 'chat') else m.chat.id, "Ainda não chegou nenhum SMS. Aguarde mais um pouco e tente de novo.", markup=markup)

# Função para manter o bot ativo com Flask (Render exige isso)
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot está online!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def resfriar_demanda():
    for k in demanda.keys():
        if demanda[k] > -4:
            demanda[k] -= 1

def run_schedule():
    schedule.every(1).hours.do(resfriar_demanda)
    while True:
        schedule.run_pending()
        time.sleep(60)

# Iniciar bot e Flask juntos
def start_bot():
    try:
        bot.remove_webhook()
    except:
        pass
    bot.infinity_polling()

# --- PAINEL ADMINISTRATIVO ---

@bot.message_handler(commands=['painel'])
def admin_panel(m):
    if m.from_user.id != ADMIN_ID:
        return
    
    total_users = len(lista_usuarios)
    saldo_total = sum(saldos.values())
    
    msg = "👑 **PAINEL SUPREMO DO ADMINISTRADOR**\n\n"
    msg += f"📊 **Estatísticas Rápidas:**\n"
    msg += f"👥 Total de Usuários: `{total_users}`\n"
    msg += f"💰 Saldo Total: `R$ {saldo_total:.2f}`\n"
    msg += f"🛠 Manutenção: `{'ATIVADA 🔴' if MODO_MANUTENCAO else 'DESATIVADA 🟢'}`\n"
    msg += f"📈 Lucro: `x{MULTIPLICADOR_LUCRO}`\n\n"
    msg += "Escolha uma categoria para gerenciar:"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("👥 Usuários", callback_data="adm_menu_users"), types.InlineKeyboardButton("📦 Pedidos", callback_data="adm_menu_orders"))
    markup.row(types.InlineKeyboardButton("⚙️ Sistema", callback_data="adm_menu_system"), types.InlineKeyboardButton("📡 Saldo API", callback_data="adm_check_api"))
    markup.row(types.InlineKeyboardButton("📢 Aviso Global", callback_data="admin_broadcast"), types.InlineKeyboardButton("💬 Suporte Direto", callback_data="adm_direct_msg"))
    
    enviar_e_limpar(m.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_menu_users')
def adm_menu_users(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("🔍 Buscar Usuário", callback_data="adm_search_user"))
    markup.row(types.InlineKeyboardButton("💰 Add Saldo Manual", callback_data="admin_add_saldo"))
    markup.row(types.InlineKeyboardButton("⬅️ Voltar", callback_data="adm_back_main"))
    enviar_e_limpar(call.message.chat.id, "👤 **GESTÃO DE USUÁRIOS**\n\nO que deseja fazer?", markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_menu_orders')
def adm_menu_orders(call):
    # Lista usuários com compras ativas
    ativos = [uid for uid, c in compras.items() if c]
    msg = "📦 **PEDIDOS ATIVOS NO MOMENTO**\n\n"
    if not ativos:
        msg += "Nenhum usuário com número ativo."
    else:
        for uid in ativos[:10]: # Mostra os 10 primeiros
            c = compras[uid]
            msg += f"• `{uid}`: {c['service']} ({c['number']})\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("❌ Estorno Forçado", callback_data="adm_force_refund"))
    markup.row(types.InlineKeyboardButton("⬅️ Voltar", callback_data="adm_back_main"))
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_menu_system')
def adm_menu_system(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(f"🛠 Manutenção: {'OFF' if MODO_MANUTENCAO else 'ON'}", callback_data="adm_toggle_maint"))
    markup.row(types.InlineKeyboardButton("📈 Alterar Multiplicador", callback_data="adm_edit_profit"))
    markup.row(types.InlineKeyboardButton("⬅️ Voltar", callback_data="adm_back_main"))
    enviar_e_limpar(call.message.chat.id, "⚙️ **CONFIGURAÇÕES DO SISTEMA**", markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_back_main')
def adm_back_main(call):
    admin_panel(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'adm_toggle_maint')
def adm_toggle_maint(call):
    global MODO_MANUTENCAO
    MODO_MANUTENCAO = not MODO_MANUTENCAO
    bot.answer_callback_query(call.id, f"Manutenção {'Ativada' if MODO_MANUTENCAO else 'Desativada'}", show_alert=True)
    adm_menu_system(call)

@bot.callback_query_handler(func=lambda call: call.data == 'adm_check_api')
def adm_check_api(call):
    url = f"https://hero-sms.com/stubs/handler_api.php?api_key={SMS_ACTIVATE_API}&action=getBalance"
    try:
        res = requests.get(url, timeout=10)
        balance = res.text.split(":")[1]
        bot.answer_callback_query(call.id, f"💰 Saldo HeroSMS: ${balance}", show_alert=True)
    except:
        bot.answer_callback_query(call.id, "❌ Erro ao consultar API", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == 'adm_edit_profit')
def adm_edit_profit(call):
    msg = enviar_e_limpar(call.message.chat.id, "📈 **ALTERAR LUCRO**\n\nDigite o novo multiplicador (Ex: 8.5):")
    bot.register_next_step_handler(msg, process_edit_profit)

def process_edit_profit(m):
    global MULTIPLICADOR_LUCRO
    try:
        val = float(m.text.replace(',', '.'))
        MULTIPLICADOR_LUCRO = val
        enviar_e_limpar(m.chat.id, f"✅ Multiplicador alterado para x{val}")
    except:
        enviar_e_limpar(m.chat.id, "❌ Valor inválido.")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_search_user')
def adm_search_user(call):
    msg = enviar_e_limpar(call.message.chat.id, "🔍 **BUSCAR USUÁRIO**\n\nDigite o ID do usuário:")
    bot.register_next_step_handler(msg, process_search_user)

def process_search_user(m):
    try:
        uid = int(m.text)
        saldo = saldos.get(uid, 0.0)
        compra = compras.get(uid)
        status_ban = "🔴 BANIDO" if bloqueios.get(uid) else "🟢 ATIVO"
        
        msg = f"👤 **DETALHES DO USUÁRIO**\n\n"
        msg += f"🆔 ID: `{uid}`\n"
        msg += f"💰 Saldo: R$ {saldo:.2f}\n"
        msg += f"🚦 Status: {status_ban}\n"
        msg += f"📦 Ativo: {compra['service'] if compra else 'Nenhum'}\n"
        
        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton("💰 Ajustar Saldo", callback_data=f"adm_adj_saldo_{uid}"))
        markup.row(types.InlineKeyboardButton("🚫 Banir/Desbanir", callback_data=f"adm_toggle_ban_{uid}"))
        markup.row(types.InlineKeyboardButton("⬅️ Voltar", callback_data="adm_menu_users"))
        
        enviar_e_limpar(m.chat.id, msg, markup=markup, parse_mode="Markdown")
    except:
        enviar_e_limpar(m.chat.id, "❌ ID inválido.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_adj_saldo_'))
def adm_adj_saldo(call):
    uid = int(call.data.split('_')[3])
    msg = enviar_e_limpar(call.message.chat.id, f"💰 **AJUSTAR SALDO - ID {uid}**\n\nDigite o novo valor total do saldo ou use + e - (Ex: `+10` ou `50`):")
    bot.register_next_step_handler(msg, lambda msg: process_adj_saldo(msg, uid))

def process_adj_saldo(m, uid):
    try:
        texto = m.text.replace(',', '.')
        if texto.startswith('+'):
            val = float(texto[1:])
            saldos[uid] = saldos.get(uid, 0.0) + val
        elif texto.startswith('-'):
            val = float(texto[1:])
            saldos[uid] = max(0, saldos.get(uid, 0.0) - val)
        else:
            val = float(texto)
            saldos[uid] = val
            
        enviar_e_limpar(m.chat.id, f"✅ Saldo do usuário `{uid}` atualizado para R$ {saldos[uid]:.2f}")
        try: bot.send_message(uid, f"💰 Seu saldo foi atualizado pelo administrador para R$ {saldos[uid]:.2f}")
        except: pass
    except:
        enviar_e_limpar(m.chat.id, "❌ Valor inválido. Use números como 10 ou +5.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_toggle_ban_'))
def adm_toggle_ban(call):
    uid = int(call.data.split('_')[3])
    if bloqueios.get(uid):
        bloqueios[uid] = False
        bot.answer_callback_query(call.id, "✅ Usuário desbanido.")
    else:
        bloqueios[uid] = True
        bot.answer_callback_query(call.id, "🚫 Usuário banido.")
    # Atualiza a view
    class FakeMsg: pass
    m = FakeMsg()
    m.text = str(uid)
    m.chat = call.message.chat
    process_search_user(m)

@bot.callback_query_handler(func=lambda call: call.data == 'adm_force_refund')
def adm_force_refund(call):
    msg = enviar_e_limpar(call.message.chat.id, "❌ **ESTORNO FORÇADO**\n\nDigite o ID do usuário para estornar a compra ativa:")
    bot.register_next_step_handler(msg, process_force_refund)

def process_force_refund(m):
    try:
        uid = int(m.text)
        if uid in compras and compras[uid]:
            compra = compras[uid]
            id_order = compra['id_order']
            provider = compra.get('provider', 'herosms')
            preco = compra.get('preco_pago', 0.0)
            
            api_cancel_number(id_order, provider)
            saldos[uid] = saldos.get(uid, 0.0) + preco
            compras[uid] = None
            
            enviar_e_limpar(m.chat.id, f"✅ Estorno de R$ {preco:.2f} realizado para `{uid}`.")
            try: bot.send_message(uid, f"⚠️ Sua compra foi cancelada e R$ {preco:.2f} foram estornados pelo administrador.")
            except: pass
        else:
            enviar_e_limpar(m.chat.id, "❌ Este usuário não tem uma compra ativa.")
    except:
        enviar_e_limpar(m.chat.id, "❌ Erro ao processar estorno.")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_direct_msg')
def adm_direct_msg(call):
    msg = enviar_e_limpar(call.message.chat.id, "💬 **SUPORTE DIRETO**\n\nDigite o ID do usuário:")
    bot.register_next_step_handler(msg, process_id_direct_msg)

def process_id_direct_msg(m):
    try:
        uid = int(m.text)
        msg = enviar_e_limpar(m.chat.id, f"✅ Usuário `{uid}` selecionado.\n\nDigite a mensagem que deseja enviar:")
        bot.register_next_step_handler(msg, lambda msg: finalize_direct_msg(msg, uid))
    except:
        enviar_e_limpar(m.chat.id, "❌ ID inválido.")

def finalize_direct_msg(m, uid):
    try:
        bot.send_message(uid, f"💬 **MENSAGEM DO SUPORTE**\n\n{m.text}", parse_mode="Markdown")
        enviar_e_limpar(m.chat.id, "✅ Mensagem enviada com sucesso!")
    except:
        enviar_e_limpar(m.chat.id, "❌ Não foi possível enviar a mensagem (Usuário bloqueou o bot?)")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_broadcast')
def handle_admin_broadcast(call):
    msg = enviar_e_limpar(call.message.chat.id, "📢 **MENSAGEM GLOBAL**\n\nDigite o texto que deseja enviar para TODOS os usuários:")
    bot.register_next_step_handler(msg, processar_broadcast)

def processar_broadcast(m):
    texto = m.text
    enviar_e_limpar(m.chat.id, f"⏳ Iniciando envio para {len(lista_usuarios)} usuários...")
    sucesso = 0
    for uid in lista_usuarios:
        try:
            bot.send_message(uid, f"📢 **AVISO IMPORTANTE**\n\n{texto}", parse_mode="Markdown")
            sucesso += 1
            time.sleep(0.1) # Evita flood
        except: pass
    enviar_e_limpar(m.chat.id, f"✅ Envio concluído!\n\nReceberam: {sucesso} usuários.")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_add_saldo')
def handle_admin_add_saldo(call):
    msg = enviar_e_limpar(call.message.chat.id, "💰 **ADICIONAR SALDO**\n\nDigite o **ID** do usuário:")
    bot.register_next_step_handler(msg, admin_processar_id_saldo)

def admin_processar_id_saldo(m):
    try:
        target_id = int(m.text)
        msg = enviar_e_limpar(m.chat.id, f"✅ Usuário `{target_id}` selecionado.\n\nDigite o **VALOR** a ser adicionado:")
        bot.register_next_step_handler(msg, lambda msg: admin_finalizar_saldo(msg, target_id))
    except:
        enviar_e_limpar(m.chat.id, "❌ ID inválido.")

def admin_finalizar_saldo(m, target_id):
    try:
        valor = float(m.text.replace(',', '.'))
        saldos[target_id] = saldos.get(target_id, 0.0) + valor
        enviar_e_limpar(m.chat.id, f"✅ Adicionado R$ {valor:.2f} ao usuário `{target_id}`.")
        try: bot.send_message(target_id, f"💰 O administrador adicionou R$ {valor:.2f} ao seu saldo!")
        except: pass
    except:
        enviar_e_limpar(m.chat.id, "❌ Valor inválido.")

# Iniciar ambos em paralelo
threading.Thread(target=start_bot, daemon=True).start()
threading.Thread(target=run_schedule, daemon=True).start()
run_flask()
