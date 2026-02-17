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

# 🔴 SEU ID DO DISCORD
MEU_ID = 1439411460378726530

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
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal:
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
        
        carrinhos_ativos[str(pagamento_id)] = {
            "canal": canal.id,
            "mensagem_id": mensagem.id,
            "usuario": user.id
        }
        
        return mensagem
        
    except Exception as e:
        print(f"❌ Erro log carrinho: {e}")
        return None

# ===============================
# FUNÇÃO PARA LOG DE PAGAMENTO CONFIRMADO
# ===============================
async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos:
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
        
        # Remover do carrinho se existir
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    await msg.delete()
                except:
                    pass
            del carrinhos_ativos[str(pagamento_id)]
        
    except Exception as e:
        print(f"❌ Erro log pagos: {e}")

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
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
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
# WEBHOOK CORRIGIDO - SEM AWAIT
# ===============================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("\n" + "🔥"*50)
    print("🔥 WEBHOOK RECEBIDO:")
    print(data)
    print("🔥"*50)
    
    try:
        # Extrair payment_id
        payment_id = None
        if data and "data" in data and "id" in data["data"]:
            payment_id = data["data"]["id"]
        
        if not payment_id:
            print("❌ Sem payment_id")
            return "OK", 200
        
        print(f"✅ Payment ID: {payment_id}")
        
        # Buscar pagamento
        payment_response = sdk.payment().get(payment_id)
        
        if payment_response["status"] != 200:
            return "OK", 200
        
        payment = payment_response["response"]
        
        if payment["status"] != "approved":
            return "OK", 200
        
        # Extrair dados
        ref = payment.get("external_reference", "")
        if not ref:
            return "OK", 200
        
        partes = ref.split('_')
        if len(partes) < 2:
            return "OK", 200
        
        produto = partes[0]
        user_id = int(partes[1])
        
        print(f"✅ Produto: {produto}, User: {user_id}")
        
        # NÃO ENTREGAR PARA O DONO
        if user_id == MEU_ID:
            print("❌ Bloqguei entrega para o dono")
            return "OK", 200
        
        # Buscar usuário
        user = bot.get_user(user_id)
        if not user:
            # Tentar fetch de forma segura
            future = asyncio.run_coroutine_threadsafe(
                bot.fetch_user(user_id), bot.loop
            )
            try:
                user = future.result(timeout=5)
            except:
                print(f"❌ Não achei usuário {user_id}")
                return "OK", 200
        
        if not user:
            return "OK", 200
        
        # DEFINIR PRODUTO INFO
        produtos = {
            "cs": {"nome": "Pack Counter Strike", "preco": 24.99},
            "rockstar": {"nome": "Conta Rockstar", "preco": 4.99}
        }
        produto_info = produtos.get(produto, produtos["cs"])
        
        # ===== ENTREGAR PRODUTO =====
        if produto == "cs":
            # Entrega automática
            future = asyncio.run_coroutine_threadsafe(
                user.send(
                    "✅ **Pagamento confirmado!**\nAqui está seu produto:",
                    file=discord.File(ARQUIVO_PRODUTO)
                ), bot.loop
            )
            future.result(timeout=10)
            print(f"✅ Produto CS enviado para {user.name}")
            
        elif produto == "rockstar":
            # Entrega manual
            future = asyncio.run_coroutine_threadsafe(
                user.send(
                    "✅ **Pagamento aprovado!**\n📦 Sua Conta Rockstar será entregue em breve por um administrador."
                ), bot.loop
            )
            future.result(timeout=10)
            print(f"✅ Aviso Rockstar enviado para {user.name}")
        
        # ===== LOG NO CANAL DE PAGOS =====
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="✅ **PAGAMENTO CONFIRMADO**",
                color=0x00ff88,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 **Cliente**", value=user.mention, inline=True)
            embed.add_field(name="📦 **Produto**", value=produto_info["nome"], inline=True)
            embed.add_field(name="💰 **Valor**", value=f"R$ {produto_info['preco']:.2f}", inline=True)
            embed.set_footer(text="🎉 Produto entregue com sucesso!")
            
            asyncio.run_coroutine_threadsafe(canal_pagos.send(embed=embed), bot.loop)
        
        # ===== REMOVER DO CARRINHO =====
        if str(payment_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(payment_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    future_msg = asyncio.run_coroutine_threadsafe(
                        canal_carrinho.fetch_message(dados["mensagem_id"]), bot.loop
                    )
                    msg = future_msg.result(timeout=5)
                    asyncio.run_coroutine_threadsafe(msg.delete(), bot.loop)
                except:
                    pass
            del carrinhos_ativos[str(payment_id)]
        
        # ===== ADICIONAR CARGO =====
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(user_id)
            if member:
                asyncio.run_coroutine_threadsafe(
                    member.remove_roles(guild.get_role(CARGO_MEMBRO)), bot.loop
                )
                asyncio.run_coroutine_threadsafe(
                    member.add_roles(guild.get_role(CARGO_CLIENTE)), bot.loop
                )
        
    except Exception as e:
        print(f"❌ ERRO: {e}")
    
    return "OK", 200

# ===============================
# START
# ===============================
def iniciar_flask():
    print("🌐 Flask online")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()
