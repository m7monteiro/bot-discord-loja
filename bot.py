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
# CONFIG — VEM DO RENDER ENV VARS
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

# ===============================
# MERCADO PAGO
# ===============================
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_pagamento_pix(user_id, produto="cs"):
    """
    Gera um pagamento PIX direto via Mercado Pago
    Retorna os dados do PIX (QR code, código copia e cola, etc)
    """
    
    # Definir produto e preço
    produtos = {
        "cs": {
            "nome": "Pack Counter Strike",
            "preco": 24.99
        },
        "rockstar": {
            "nome": "Conta Rockstar", 
            "preco": 4.99
        }
    }
    
    produto_info = produtos.get(produto, produtos["cs"])
    
    # Criar pagamento PIX
    payment_data = {
        "transaction_amount": produto_info["preco"],
        "description": produto_info["nome"],
        "payment_method_id": "pix",
        "payer": {
            "email": f"cliente_{user_id}@temp.com"
        },
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
                "payment_id": payment["id"],
                "produto": produto_info["nome"],
                "preco": produto_info["preco"],
                "referencia": payment["external_reference"]
            }
        else:
            print(f"❌ Erro MP: {result}")
            return None
    except Exception as e:
        print(f"❌ Erro ao criar PIX: {e}")
        return None

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

    async def on_member_join(self, member):
        cargo = member.guild.get_role(CARGO_MEMBRO)
        if cargo:
            await member.add_roles(cargo)
            print("👤 Cargo MEMBRO aplicado")

bot = Bot()

# ===============================
# CLASSE DO BOTÃO DE COMPRA
# ===============================
class BotaoComprar(discord.ui.View):
    def __init__(self, produto: str, user_id: int):
        super().__init__(timeout=300)  # 5 minutos de timeout
        self.produto = produto
        self.user_id = user_id
    
    @discord.ui.button(label="🛒 Comprar Agora", style=discord.ButtonStyle.green, emoji="💳")
    async def botao_comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Desabilitar o botão para não clicar de novo
        button.disabled = True
        await interaction.response.edit_message(view=self)
        
        # Enviar uma mensagem "processando"
        await interaction.followup.send("⏳ Gerando seu pagamento PIX...", ephemeral=True)
        
        # Gerar PIX
        pix_data = criar_pagamento_pix(self.user_id, self.produto)
        
        if not pix_data:
            await interaction.followup.send("❌ Erro ao gerar pagamento. Tente novamente mais tarde.", ephemeral=True)
            return
        
        # Criar embed do PIX
        embed_pix = discord.Embed(
            title="🧾 Pagamento PIX Gerado!",
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
        
        embed_pix.add_field(
            name="📋 Código PIX", 
            value=f"```{pix_data['qr_code']}```", 
            inline=False
        )
        
        embed_pix.add_field(
            name="✅ Como pagar",
            value=(
                "1️⃣ Copie o código PIX acima\n"
                "2️⃣ Abra o app do seu banco\n"
                "3️⃣ Escolha a opção PIX copia e cola\n"
                "4️⃣ Cole o código e confirme o pagamento"
            ),
            inline=False
        )
        
        embed_pix.set_footer(text="O produto será entregue automaticamente após a confirmação!")
        
        # Converter QR code para imagem
        qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
        
        # Enviar PIX
        with BytesIO(qr_image_data) as image_binary:
            image_binary.seek(0)
            file = discord.File(fp=image_binary, filename="qrcode.png")
            await interaction.followup.send(embed=embed_pix, file=file)

# ===============================
# COMANDOS DE COMPRA
# ===============================
@bot.tree.command(name="comprar", description="Comprar Pack Counter Strike")
async def comprar(interaction: discord.Interaction):
    # Embed do produto CS com a imagem original
    embed = discord.Embed(
        title="🔥 Cheat Counter Strike",
        description="✅ Acesso completo\n✅ Arquivos exclusivos\n✅ Suporte VIP\n✅ Entrega Automática",
        color=0x00ff88
    )
    embed.add_field(name="💰 Preço", value="R$ 24,99", inline=False)
    embed.set_image(url="https://cdn.discordapp.com/attachments/1472115881436905483/1472691849130016953/VELAR_1.png")
    embed.set_footer(text="Legend Store — Todos os direitos reservados ©")
    
    # Criar view com botão
    view = BotaoComprar(produto="cs", user_id=interaction.user.id)
    
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="comprar_rockstar", description="Comprar Conta Rockstar")
async def comprar_rockstar(interaction: discord.Interaction):
    # Embed do produto Rockstar com a imagem original
    embed = discord.Embed(
        title="🎮 Conta Rockstar",
        description="✅ Conta pronta\n✅ Entrega manual\n✅ Garantia",
        color=0x3498db
    )
    embed.add_field(name="💰 Preço", value="R$ 4,99", inline=False)
    embed.set_image(url="https://cdn.discordapp.com/attachments/1472115881436905483/1472688688675684526/VELAR_2.png?ex=69937bb8&is=69922a38&hm=0acb656efc6464acb1a63c2b71a98e66aebedf68f17326bb1847945173131320")
    embed.set_footer(text="Legend Store — Todos os direitos reservados ©")
    
    # Criar view com botão
    view = BotaoComprar(produto="rockstar", user_id=interaction.user.id)
    
    await interaction.response.send_message(embed=embed, view=view)

# ===============================
# WEBHOOK
# ===============================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 webhook recebido:", data)

    try:
        if "data" in data and "id" in data["data"]:
            payment_id = data["data"]["id"]
            payment = sdk.payment().get(payment_id)["response"]
            
            print(f"💰 Status: {payment['status']}")
            
            if payment["status"] == "approved":
                ref = payment["external_reference"]
                partes = ref.split('_')
                produto = partes[0]
                user_id = int(partes[1])
                
                print(f"✅ Pagamento aprovado! Produto: {produto}, Usuário: {user_id}")
                
                if produto == "cs":
                    asyncio.run_coroutine_threadsafe(enviar_produto(user_id), bot.loop)
                elif produto == "rockstar":
                    asyncio.run_coroutine_threadsafe(enviar_produto_manual(user_id), bot.loop)
    except Exception as e:
        print("❌ Erro no webhook:", e)

    return "OK", 200

# ===============================
# ENTREGA AUTOMÁTICA CS
# ===============================
async def enviar_produto(user_id):
    user = await bot.fetch_user(user_id)
    await user.send(
        "✅ Pagamento confirmado! Aqui está seu produto:",
        file=discord.File(ARQUIVO_PRODUTO)
    )
    
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(user_id)
    if member:
        await member.remove_roles(guild.get_role(CARGO_MEMBRO))
        await member.add_roles(guild.get_role(CARGO_CLIENTE))
    print("📦 Produto CS entregue")

# ===============================
# ENTREGA MANUAL ROCKSTAR
# ===============================
async def enviar_produto_manual(user_id):
    user = await bot.fetch_user(user_id)
    await user.send(
        "✅ Pagamento aprovado!\n"
        "📦 Sua Conta Rockstar será entregue em breve por um administrador."
    )
    print("📨 Aviso manual enviado")

# ===============================
# FLASK + START
# ===============================
def iniciar_flask():
    print("🌐 Flask online")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def start_all():
    threading.Thread(target=iniciar_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_all()
