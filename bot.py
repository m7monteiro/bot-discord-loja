import discord
from discord import app_commands
import mercadopago
from flask import Flask, request
import threading
import asyncio
import os
import sys
import time
import base64
from datetime import datetime
from io import BytesIO

print("🔧 Iniciando bot...")

# ===============================
# CONFIG
# ===============================
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MP_ACCESS_TOKEN = os.environ["MP_ACCESS_TOKEN"]
WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL",
    "https://bot-discord-loja-eg7u.onrender.com/webhook"
)

ARQUIVO_PRODUTO = "produto.txt"

if not os.path.exists(ARQUIVO_PRODUTO):
    print("❌ produto.txt não encontrado")
    sys.exit()

GUILD_ID = 1472114509068898367
CARGO_MEMBRO = 1472666559049633952
CARGO_CLIENTE = 1472666841515032676

# IDs dos canais de log
CANAL_CARRINHOS = 1473180070851117108  # Canal #carrinhos-ativos
CANAL_PAGOS = 1473182832225554554      # Canal #pagamentos-confirmados

# Dicionário para armazenar mensagens de carrinho
carrinhos_ativos = {}  # {pagamento_id: {"canal": canal, "mensagem_id": id, "usuario": user_id}}

# ===============================
# MERCADO PAGO
# ===============================
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_pagamento_pix(user_id, produto="cs"):
    """Gera pagamento PIX via Mercado Pago"""
    
    produtos = {
        "cs": {"nome": "Pack Counter Strike", "preco": 24.99},
        "rockstar": {"nome": "Conta Rockstar", "preco": 4.99}
    }
    
    produto_info = produtos.get(produto, produtos["cs"])
    
    payment_data = {
        "transaction_amount": produto_info["preco"],
        "description": produto_info["nome"],
        "payment_method_id": "pix",
        "payer": {"email": f"cliente_{user_id}@temp.com"},
        "external_reference": f"{produto}_{user_id}_{int(time.time())}",
        "notification_url": WEBHOOK_URL
    }
    
    try:
        result = sdk.payment().create(payment_data)
        
        if result["status"] == 201:
            payment = result["response"]
            pix_data = payment["point_of_interaction"]["transaction_data"]
            
            return {
                "qr_code": pix_data["qr_code"],
                "qr_code_base64": pix_data["qr_code_base64"],
                "expiration": payment["date_of_expiration"],
                "produto": produto_info["nome"],
                "preco": produto_info["preco"],
                "payment_id": payment["id"]
            }
    except Exception as e:
        print(f"❌ Erro PIX: {e}")
        return None
    
    return None

# ===============================
# FUNÇÃO PARA LOG DE CARRINHOS
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    """
    Envia uma mensagem no canal de carrinhos ativos
    """
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal:
            print("❌ Canal de carrinhos não encontrado!")
            return None
        
        embed = discord.Embed(
            title="🛒 **NOVO CARRINHO ATIVO**",
            color=0xffaa00,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="👤 **Cliente**", value=user.mention, inline=True)
        embed.add_field(name="📦 **Produto**", value=produto_nome, inline=True)
        embed.add_field(name="💰 **Valor**", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="⏰ **Horário**", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="🆔 **Pagamento**", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="⏳ Aguardando pagamento...")
        
        mensagem = await canal.send(embed=embed)
        print(f"✅ Log enviado para canal de carrinhos - Pagamento: {pagamento_id}")
        
        # Armazenar a mensagem para remover depois
        carrinhos_ativos[str(pagamento_id)] = {
            "canal": canal.id,
            "mensagem_id": mensagem.id,
            "usuario": user.id
        }
        
        return mensagem
        
    except Exception as e:
        print(f"❌ Erro ao enviar log de carrinho: {e}")
        return None

# ===============================
# FUNÇÃO PARA REMOVER DO CARRINHO E LOG DE PAGAMENTO
# ===============================
async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id):
    """
    Remove a mensagem do canal de carrinhos e envia para o canal de pagos
    """
    try:
        # 1️⃣ REMOVER DO CANAL DE CARRINHOS
        if str(pagamento_id) in carrinhos_ativos:
            dados_carrinho = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados_carrinho["canal"])
            
            if canal_carrinho:
                try:
                    mensagem = await canal_carrinho.fetch_message(dados_carrinho["mensagem_id"])
                    await mensagem.delete()
                    print(f"✅ Mensagem do carrinho removida (Pagamento: {pagamento_id})")
                except Exception as e:
                    print(f"⚠️ Mensagem do carrinho já não existe: {e}")
            
            # Remover do dicionário
            del carrinhos_ativos[str(pagamento_id)]
        
        # 2️⃣ ENVIAR PARA O CANAL DE PAGOS
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos:
            print("❌ Canal de pagos não encontrado!")
            return
        
        embed = discord.Embed(
            title="✅ **PAGAMENTO CONFIRMADO**",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="👤 **Cliente**", value=user.mention, inline=True)
        embed.add_field(name="📦 **Produto**", value=produto_nome, inline=True)
        embed.add_field(name="💰 **Valor**", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="⏰ **Horário**", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="🆔 **Pagamento**", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="🎉 Produto entregue com sucesso!")
        
        await canal_pagos.send(embed=embed)
        print(f"✅ Log de pagamento enviado para canal de pagos - Pagamento: {pagamento_id}")
        
    except Exception as e:
        print(f"❌ Erro ao processar pagamento: {e}")

# ===============================
# DISCORD
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands sincronizados")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")

bot = Bot()

# ===============================
# CLASSE DO BOTÃO DE COPIAR PIX
# ===============================
class CopiarPIXView(discord.ui.View):
    def __init__(self, codigo_pix: str):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix

    @discord.ui.button(label="📋 Copiar código PIX", style=discord.ButtonStyle.primary)
    async def copiar_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"```{self.codigo_pix}```", 
            ephemeral=True
        )

# ===============================
# CLASSE DO BOTÃO DE COMPRA
# ===============================
class BotaoComprar(discord.ui.View):
    def __init__(self, produto: str, user_id: int):
        super().__init__(timeout=300)
        self.produto = produto
        self.user_id = user_id
    
    @discord.ui.button(label="🛒 Comprar Agora", style=discord.ButtonStyle.green, emoji="💳")
    async def botao_comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        
        await interaction.response.send_message("📨 **Enviando informações no seu privado...**", ephemeral=True)
        
        button.disabled = True
        await interaction.edit_original_response(view=self)
        
        user = interaction.user
        
        try:
            pix_data = criar_pagamento_pix(self.user_id, self.produto)
            
            if not pix_data:
                await user.send("❌ **Erro ao gerar pagamento.** Tente novamente mais tarde.")
                return
            
            # ===== LOG NO CANAL DE CARRINHOS =====
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            # ===== EMBED DO PIX NO PRIVADO =====
            embed_pix = discord.Embed(
                title="🧾 **PAGAMENTO PIX**",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
        except discord.Forbidden:
            await interaction.followup.send("❌ **Não consegui te enviar mensagem no privado!**\nVerifique se você permite DMs de membros do servidor.", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro: {e}")
            await user.send("❌ **Ocorreu um erro inesperado.** Contate um administrador.")

# ===============================
# COMANDOS
# ===============================
@bot.tree.command(name="comprar", description="Comprar Pack Counter Strike")
async def comprar(interaction: discord.Interaction):
    
    embed = discord.Embed(
        title="🔥 **Cheat Counter Strike**",
        description="✅ Acesso completo\n✅ Arquivos exclusivos\n✅ Suporte VIP\n✅ Entrega Automática",
        color=0x00ff88
    )
    embed.add_field(name="💰 **Preço**", value="R$ 24,99", inline=False)
    embed.set_image(url="https://i.imgur.com/EuTrxjn.png")
    embed.set_footer(text="Legend Store — Clique no botão para pagar via PIX")
    
    view = BotaoComprar(produto="cs", user_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="comprar_rockstar", description="Comprar Conta Rockstar")
async def comprar_rockstar(interaction: discord.Interaction):
    
    embed = discord.Embed(
        title="🎮 **Conta Rockstar**",
        description="✅ Conta pronta\n✅ Entrega Automática\n✅ Garantia",
        color=0x3498db
    )
    embed.add_field(name="💰 **Preço**", value="R$ 4,99", inline=False)
    embed.set_image(url="https://i.imgur.com/ppmITej.png")
    embed.set_footer(text="Legend Store — Clique no botão para pagar via PIX")
    
    view = BotaoComprar(produto="rockstar", user_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)

# ===============================
# FUNÇÕES DE ENTREGA (DEFINIDAS ANTES DO WEBHOOK)
# ===============================

async def entregar_produto_cs(user_id, pagamento_id, produto_info):
    try:
        # Buscar usuário pelo ID
        user = await bot.fetch_user(user_id)
        
        if not user:
            print(f"❌ Usuário {user_id} não encontrado no Discord")
            return
        
        print(f"✅ Entregando produto para: {user.name} (ID: {user.id})")
        
        # Enviar produto no privado do cliente
        await user.send(
            "✅ **Pagamento confirmado!**\nAqui está seu produto:",
            file=discord.File(ARQUIVO_PRODUTO)
        )
        
        # Log no canal de pagos
        await log_pagamento_confirmado(
            user=user,
            produto_nome=produto_info["nome"],
            valor=produto_info["preco"],
            pagamento_id=pagamento_id
        )
        
        # Adicionar cargo de cliente
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(user_id)
            if member:
                await member.remove_roles(guild.get_role(CARGO_MEMBRO))
                await member.add_roles(guild.get_role(CARGO_CLIENTE))
        
        print(f"📦 Produto CS entregue com sucesso para {user.name}")
        
    except discord.Forbidden:
        print(f"❌ DM fechada para {user.name}")
        # Notificar no canal de logs
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            await canal_pagos.send(f"⚠️ **ATENÇÃO:** Não consegui enviar o produto para {user.mention}! DM fechada.")
    except Exception as e:
        print(f"❌ Erro ao entregar produto CS: {e}")
        import traceback
        traceback.print_exc()

async def entregar_produto_rockstar(user_id, pagamento_id, produto_info):
    try:
        # Buscar usuário pelo ID
        user = await bot.fetch_user(user_id)
        
        if not user:
            print(f"❌ Usuário {user_id} não encontrado no Discord")
            return
        
        print(f"✅ Processando compra manual para: {user.name} (ID: {user.id})")
        
        # Avisar que a entrega será manual
        await user.send(
            "✅ **Pagamento aprovado!**\n📦 Sua Conta Rockstar será entregue em breve por um administrador."
        )
        
        # Log no canal de pagos
        await log_pagamento_confirmado(
            user=user,
            produto_nome=produto_info["nome"],
            valor=produto_info["preco"],
            pagamento_id=pagamento_id
        )
        
        print(f"📨 Aviso manual enviado para {user.name}")
        
    except discord.Forbidden:
        print(f"❌ DM fechada para {user.name}")
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            await canal_pagos.send(f"⚠️ **ATENÇÃO:** Não consegui avisar {user.mention} sobre a compra! DM fechada.")
    except Exception as e:
        print(f"❌ Erro ao processar compra manual: {e}")
        import traceback
        traceback.print_exc()

# ===============================
# WEBHOOK (AGORA AS FUNÇÕES JÁ EXISTEM)
# ===============================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 webhook recebido:", data)
    
    try:
        if "data" in data and "id" in data["data"]:
            payment_id = data["data"]["id"]
            
            # Buscar detalhes do pagamento
            payment_response = sdk.payment().get(payment_id)
            
            if payment_response["status"] == 200:
                payment = payment_response["response"]
                
                if payment["status"] == "approved":
                    # Extrair external_reference
                    ref = payment.get("external_reference", "")
                    print(f"📌 external_reference: {ref}")
                    
                    if ref:
                        partes = ref.split('_')
                        if len(partes) >= 2:
                            produto = partes[0]
                            try:
                                user_id = int(partes[1])
                                print(f"✅ Pagamento aprovado! Produto: {produto}, User ID: {user_id}")
                                
                                # Buscar dados do produto
                                produtos = {
                                    "cs": {"nome": "Pack Counter Strike", "preco": 24.99},
                                    "rockstar": {"nome": "Conta Rockstar", "preco": 4.99}
                                }
                                produto_info = produtos.get(produto, produtos["cs"])
                                
                                # ===== CHAMAR A ENTREGA DO PRODUTO =====
                                if produto == "cs":
                                    print("🔍 Chamando entregar_produto_cs...")
                                    asyncio.run_coroutine_threadsafe(
                                        entregar_produto_cs(user_id, payment_id, produto_info),
                                        bot.loop
                                    )
                                elif produto == "rockstar":
                                    print("🔍 Chamando entregar_produto_rockstar...")
                                    asyncio.run_coroutine_threadsafe(
                                        entregar_produto_rockstar(user_id, payment_id, produto_info),
                                        bot.loop
                                    )
                            except ValueError:
                                print(f"❌ Erro ao converter user_id: {partes[1]}")
    except Exception as e:
        print("❌ Erro webhook:", e)
        import traceback
        traceback.print_exc()
    
    return "OK", 200

# ===============================
# START
# ===============================
def iniciar_flask():
    print("🌐 Flask online na porta 10000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()
