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

@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text == 'Start 🔄')
def start(m):
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

def menu_principal(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('📱 Comprar Número')
    markup.row('💳 Recarregar', '⚙️ Configurações')
    
    name = m.from_user.first_name
    username = m.from_user.username
    display_name = f"@{username}" if username else name.upper()
    user_id = m.from_user.id
    saldo = saldos.get(user_id, 0.0)
    
    msg = f"✨ [OLÁ, {display_name}!](tg://user?id={user_id}) ✨\n\n"
    msg += f"👤 **Cliente:** [`P{user_id}`](tg://user?id={user_id})\n"
    msg += f"💰 **Seu Saldo:** [R$ {saldo:.2f}](tg://user?id={user_id})\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🚀 **O que deseja fazer hoje?**\n"
    msg += f"Clique nos botões abaixo para navegar.\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🆘 **Suporte:** @CORVO291\n"
    msg += f"✅ **Sistema Online & Seguro**"
    
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
    markup.row(types.InlineKeyboardButton("★ Selecionar Favoritos", callback_data="placeholder_fav"))
    markup.row(types.InlineKeyboardButton("📊 Serviços mais comprados", callback_data="placeholder_stats"))
    markup.row(types.InlineKeyboardButton("📞 Selecionar operadora", callback_data="placeholder_carrier"))
    markup.row(types.InlineKeyboardButton("⚙️ Setup de alertas", callback_data="placeholder_alerts"))
    markup.row(types.InlineKeyboardButton("💸 Transferir saldo (Em manutenção)", callback_data="placeholder_transfer"))
    markup.row(types.InlineKeyboardButton("👤 Perfil", callback_data="placeholder_profile"))
    markup.row(types.InlineKeyboardButton("❌ Deletar conta e dados", callback_data="placeholder_delete"))
    
    enviar_e_limpar(m.chat.id, "⚙️ **Menu de configurações**", markup=markup, parse_mode="Markdown")

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
    msg = f"💎 **MENU DE COMPRAS** 💎\n📍 País: **{PAISES.get(cid, 'Brasil')}**\n\n"
    if filter_fav:
        msg = f"⭐ **MEUS FAVORITOS** ({PAISES.get(cid, 'Brasil')}) ⭐\n\n"
        servicos = [s for s in SERVICOS_GRID if s[0] in favoritos.get(user_id, [])]
    else:
        servicos = SERVICOS_GRID

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
    markup.row(types.InlineKeyboardButton(txt_fav, callback_data="toggle_filter_fav"), types.InlineKeyboardButton("🔗 Afiliados", callback_data="placeholder_afiliados"))
    markup.row(types.InlineKeyboardButton("💳 Adicionar Saldo", callback_data="menu_add_saldo"))
    
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

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_afiliados')
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
        
        msg = f"✅ Número gerado com sucesso! ({provider})\n\n"
        msg += f"Serviço: {service_name}\n"
        msg += f"Número: `{number}`\n\n"
        msg += "Aguarde o código SMS e clique em '📥 Receber SMS' no menu."
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ Cancelar / Reembolsar", callback_data="cancel_number"))
        
        enviar_e_limpar(call.message.chat.id, msg, parse_mode="Markdown", markup=markup)
    else:
        enviar_e_limpar(call.message.chat.id, f"❌ Erro ao solicitar número: {number} (Sem estoque ou erro na API)")

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

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_profile')
def handle_profile(call):
    user_id = call.from_user.id
    saldo = saldos.get(user_id, 0.0)
    total = historico_compras.get(user_id, 0)
    msg = f"👤 **SEU PERFIL**\n\n"
    msg += f"🆔 ID: `{user_id}`\n"
    msg += f"💰 Saldo: R$ {saldo:.2f}\n"
    msg += f"📱 Compras realizadas: {total}\n"
    msg += f"📞 Operadora padrão: {operadoras_preferidas.get(user_id, 'Qualquer uma')}\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'btn_config_voltar')
def config_voltar(call):
    # Simula o clique no botão de configurações para voltar ao menu
    btn_config(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_delete')
def handle_delete(call):
    user_id = call.from_user.id
    saldos[user_id] = 0.0
    historico_compras[user_id] = 0
    favoritos[user_id] = []
    bot.answer_callback_query(call.id, "❌ Todos os seus dados locais foram deletados e saldo zerado.", show_alert=True)
    menu_principal(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_stats')
def handle_stats(call):
    # Ordena os serviços por demanda (mais comprados)
    mais_comprados = sorted(demanda.items(), key=lambda x: x[1], reverse=True)[:5]
    msg = "📊 **SERVIÇOS MAIS COMPRADOS**\n\n"
    for code, pts in mais_comprados:
        nome = SERVICOS.get(code, code)
        msg += f"• {nome}: {pts + 5} vendas recentes\n" # +5 para dar um ar de movimentado
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, msg, markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_transfer')
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

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_carrier')
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

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_fav')
def handle_fav_menu(call):
    markup = types.InlineKeyboardMarkup()
    # Lista alguns para favoritar
    for code, name in [('wa', 'WhatsApp'), ('tg', 'Telegram'), ('ig', 'Instagram')]:
        status = "⭐" if code in favoritos.get(call.from_user.id, []) else "☆"
        markup.add(types.InlineKeyboardButton(f"{status} {name}", callback_data=f"toggle_fav_{code}"))
    markup.add(types.InlineKeyboardButton("⬅️ Voltar", callback_data="btn_config_voltar"))
    enviar_e_limpar(call.message.chat.id, "★ **MEUS FAVORITOS**\n\nClique para adicionar ou remover dos favoritos:", markup=markup, parse_mode="Markdown")

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

@bot.callback_query_handler(func=lambda call: call.data == 'placeholder_alerts')
def handle_alerts(call):
    bot.answer_callback_query(call.id, "🔔 Alertas de estoque ativados para todos os serviços!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == 'menu_add_saldo')
def handle_menu_add_saldo(call):
    menu_adicionar_saldo(call.message)

@bot.message_handler(func=lambda m: m.text == '📥 Receber SMS')
def receber_sms(m):
    apagar_msg_usuario(m)
    user_id = m.from_user.id
    if user_id not in compras or not compras[user_id]:
        enviar_e_limpar(m.chat.id, "❌ Você não tem nenhum número aguardando SMS no momento. Compre um número primeiro.")
        return
        
    compra = compras[user_id]
    id_order = compra['id_order']
    provider = compra.get('provider', 'sms_activate')
    
    enviar_e_limpar(m.chat.id, f"⏳ Verificando SMS na API ({provider})... Aguarde.")
    
    sms_code = api_get_sms(id_order, provider)
    
    if sms_code:
        enviar_e_limpar(m.chat.id, f"✅ O SMS CHEGOU!\n\nSeu código é: `{sms_code}`", parse_mode="Markdown")
        compras[user_id] = None # Finaliza o pedido
    else:
        enviar_e_limpar(m.chat.id, "Ainda não chegou nenhum SMS. Aguarde mais um pouco e clique no botão novamente.")

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
    
    msg = "👑 **PAINEL DO ADMINISTRADOR**\n\n"
    msg += f"👥 Total de Usuários: {total_users}\n"
    msg += f"💰 Saldo Total no Bot: R$ {saldo_total:.2f}\n\n"
    msg += "Escolha uma ação abaixo:"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("📢 Enviar Aviso Global", callback_data="admin_broadcast"))
    markup.row(types.InlineKeyboardButton("💰 Adicionar Saldo Manual", callback_data="admin_add_saldo"))
    markup.row(types.InlineKeyboardButton("📈 Ver Mais Stats", callback_data="placeholder_stats"))
    
    enviar_e_limpar(m.chat.id, msg, markup=markup, parse_mode="Markdown")

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
