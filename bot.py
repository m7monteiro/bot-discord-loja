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

# 🟡 ID DO CANAL DE CARRINHOS ATIVOS
CANAL_CARRINHOS = 1473180070851117108  # Canal #carrinhos-ativos

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
                "payment_id": payment["id"]  # Adicionamos o ID do pagamento
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
    quando alguém clica em comprar
    """
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal:
            print("❌ Canal de carrinhos não encontrado!")
            return
        
        embed = discord.Embed(
            title="🛒 **NOVO CARRINHO ATIVO**",
            color=0xffaa00,  # Amarelo
            timestamp=datetime.now()
        )
        
        embed.add_field(name="👤 **Cliente**", value=user.mention, inline=True)
        embed.add_field(name="📦 **Produto**", value=produto_nome, inline=True)
        embed.add_field(name="💰 **Valor**", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="⏰ **Horário**", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="🆔 **Pagamento**", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="⏳ Aguardando pagamento...")
        
        await canal.send(embed=embed)
        print(f"✅ Log enviado para canal de carrinhos")
        
    except Exception as e:
        print(f"❌ Erro ao enviar log de carrinho: {e}")

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
        super().__init__(timeout=300)  # 5 minutos
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
        
        # AVISAR QUE VAI PRO PRIVADO
        await interaction.response.send_message("📨 **Enviando informações no seu privado...**", ephemeral=True)
        
        # Desabilitar botão (opcional)
        button.disabled = True
        await interaction.edit_original_response(view=self)
        
        # Buscar usuário para DM
        user = interaction.user
        
        try:
            # Gerar PIX
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
            
            # Calcular expiração
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            # Converter QR Code para imagem
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            
            # Criar view com botão de copiar
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            # Enviar TUDO no privado (embed + qr + botão de copiar)
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
        except discord.Forbidden:
            # Se o usuário bloqueou DM
            await interaction.followup.send("❌ **Não consegui te enviar mensagem no privado!**\nVerifique se você permite DMs de membros do servidor.", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro: {e}")
            await user.send("❌ **Ocorreu um erro inesperado.** Contate um administrador.")

# ===============================
# COMANDOS - SÓ MOSTRAM O PRODUTO
# ===============================
@bot.tree.command(name="comprar", description="Comprar Pack Counter Strike")
async def comprar(interaction: discord.Interaction):
    
    embed = discord.Embed(
        title="🔥 **Cheat Counter Strike**",
        description="✅ Acesso completo\n✅ Arquivos exclusivos\n✅ Suporte VIP\n✅ Entrega Automática",
        color=0x00ff88
    )
    embed.add_field(name="💰 **Preço**", value="R$ 24,99", inline=False)
    embed.set_image(url="https://i.imgur.com/EuTrxjn.png")  # SUA ARTE AQUI
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
    embed.set_image(url="https://i.imgur.com/ppmITej.png")  # SUA ARTE AQUI
    embed.set_footer(text="Legend Store — Clique no botão para pagar via PIX")
    
    view = BotaoComprar(produto="rockstar", user_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)

# ===============================
# WEBHOOK E ENTREGAS
# ===============================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 webhook:", data)
    
    try:
        if "data" in data and "id" in data["data"]:
            payment_id = data["data"]["id"]
            payment = sdk.payment().get(payment_id)["response"]
            
            if payment["status"] == "approved":
                ref = payment["external_reference"]
                partes = ref.split('_')
                produto = partes[0]
                user_id = int(partes[1])
                
                print(f"✅ Pagamento aprovado! Produto: {produto}")
                
                if produto == "cs":
                    asyncio.run_coroutine_threadsafe(enviar_produto(user_id), bot.loop)
                elif produto == "rockstar":
                    asyncio.run_coroutine_threadsafe(enviar_produto_manual(user_id), bot.loop)
    except Exception as e:
        print("❌ Erro webhook:", e)
    
    return "OK", 200

async def enviar_produto(user_id):
    user = await bot.fetch_user(user_id)
    await user.send(
        "✅ **Pagamento confirmado!**\nAqui está seu produto:",
        file=discord.File(ARQUIVO_PRODUTO)
    )
    
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(user_id)
    if member:
        await member.remove_roles(guild.get_role(CARGO_MEMBRO))
        await member.add_roles(guild.get_role(CARGO_CLIENTE))
    print("📦 Produto CS entregue")

async def enviar_produto_manual(user_id):
    user = await bot.fetch_user(user_id)
    await user.send(
        "✅ **Pagamento aprovado!**\n📦 Sua Conta Rockstar será entregue em breve por um administrador."
    )
    print("📨 Aviso manual enviado")

# ===============================
# START
# ===============================
def iniciar_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()
